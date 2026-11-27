"""Ablauf: fetch -> normalisieren -> SQLite (Dedupe) -> nur neue/geänderte -> Bedingungen ->
LLM-Analyse -> Python-Rechnung -> Entscheidung -> Benachrichtigung."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from dealradar.calc import CalcResult, compute
from dealradar.conditions import Conditions, load_conditions
from dealradar.config import Config
from dealradar.db import DB
from dealradar.http import Fetcher
from dealradar.models import DealAnalysis, RawDeal, check_frist, sanitize
from dealradar.notify import AlertItem, build_channels
from dealradar.scoring import Decision, decide
from dealradar.sources import fetch_source

log = logging.getLogger("dealradar")

AMOUNT_RX = re.compile(
    r"(?:(?:EUR|€)\s*(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?))"
    r"|(?:(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:€|EUR|Euro\b))")


def amounts(text: str) -> list[float]:
    out = []
    for m in AMOUNT_RX.finditer(text):
        s = (m.group(1) or m.group(2)).replace(".", "").replace(",", ".")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def offer_key(d: RawDeal) -> str:
    """Grober Schlüssel für dieselbe Aktion über verschiedene Quellen hinweg."""
    prov = re.sub(r"[^a-z0-9]", "", (d.provider_hint or d.title.split(":")[0]).lower().split(" ")[0])
    top = max(amounts(d.title) or [0])
    return f"{prov}|{top:.0f}"


def passes_prefilter(d: RawDeal, cfg: Config) -> bool:
    from dealradar.exclude import excluded
    if excluded(d.title, d.provider_hint):          # Energie, Internet/Mobilfunk, Verträge: nie analysieren
        return False
    pf = cfg["prefilter"]
    if not pf.get("enabled", True):
        return True
    blob = f"{d.title} {d.text}"
    if any(k.lower() in blob.lower() for k in pf.get("keywords_always", [])):
        return True
    if re.search(r"\d+(?:,\d+)?\s*%\s*(?:p\.\s?a\.|Zins)", blob):
        return True
    return max(amounts(blob) or [0]) >= cfg["min_net_gain_eur"] * pf["min_amount_factor"]


@dataclass
class RunSummary:
    fetched: int = 0
    new: int = 0
    changed: int = 0
    analyzed: int = 0
    prefiltered: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    alerts: list[AlertItem] = field(default_factory=list)
    below: list[tuple[str, str]] = field(default_factory=list)
    llm_cost_eur: float = 0.0
    budget_hit: bool = False
    fatal: bool = False


def setup_logging(cfg: Config, verbose: bool = False) -> None:
    lf = cfg.path("log_file")
    lf.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(lf, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO if verbose else logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(fh)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _warn(channels: list, db: DB, key: str, title: str, text: str) -> None:
    log.warning("%s: %s", title, text)
    if db.warning_sent_recently(key):
        return
    for ch in channels:
        try:
            ch.send_warning(title, text)
        except Exception as e:
            log.error("Warnung über %s fehlgeschlagen: %s", getattr(ch, "name", ch), e)
    db.mark_warning(key)


def run(cfg: Config, *, dry_run: bool = False, only_source: str | None = None,
        limit: int | None = None, llm_factory=None, fetcher: Fetcher | None = None,
        db: DB | None = None, today: date | None = None) -> RunSummary:
    today = today or date.today()
    s = RunSummary()
    db = db or DB(cfg.path("db"), in_memory_copy=dry_run)
    h = cfg["http"]
    fetcher = fetcher or Fetcher(h["user_agent"], h["min_delay_seconds"], h["timeout_seconds"],
                                 cfg.path("cache_dir"), h["cache_ttl_hours"])
    channels = build_channels(cfg["notify"]["channels"], cfg, dry_run)

    # ---------- 1. Fetch ----------
    raws: list[RawDeal] = []
    for name, scfg in cfg.sources.items():
        if not scfg.get("enabled", True) or (only_source and name != only_source):
            continue
        try:
            found = fetch_source(fetcher, name, scfg)
            db.record_source_run(name, True, len(found))
            if not found:
                _warn(channels, db, f"zero:{name}", f"Quelle {name} liefert 0 Einträge",
                      "Der Parser hat nichts gefunden – vermutlich hat sich die Seitenstruktur geändert.")
            raws.extend(found)
        except Exception as e:
            log.error("Quelle %s fehlgeschlagen: %s", name, e)
            db.record_source_run(name, False, 0, str(e))
            s.errors.append(f"{name}: {e}")
        since = db.source_failing_since(name)
        if since and (datetime.now(timezone.utc) - since).days >= cfg["health"]["source_down_warn_days"]:
            _warn(channels, db, f"down:{name}", f"Quelle {name} fällt seit {since:%d.%m.%Y} aus",
                  "Seit mindestens drei Tagen keine Einträge. Bitte Quelle/Parser prüfen.")
    s.fetched = len(raws)

    # ---------- 2. Dedupe ----------
    todo: list[tuple[RawDeal, str]] = []
    seen_offers: dict[str, str] = {}
    by_id = {d.deal_id: d for d in raws}
    for d in raws:
        ok = offer_key(d)
        change = db.upsert(d, ok)
        if change == "unveraendert":
            continue
        s.new += change == "neu"
        s.changed += change == "geaendert"
        kind = "update" if db.was_alerted(d.deal_id) else "neu"
        if ok in seen_offers or (kind == "neu" and db.offer_alerted_elsewhere(ok, d.deal_id)):
            db.set_status(d.deal_id, "duplicate", f"gleiche Aktion wie {seen_offers.get(ok, 'früherer Fund')}")
            s.duplicates += 1
            continue
        seen_offers[ok] = d.deal_id
        if not passes_prefilter(d, cfg):
            db.set_status(d.deal_id, "skipped", "Vorfilter: kein relevanter Betrag/Zins erkennbar")
            s.prefiltered += 1
            continue
        todo.append((d, kind))
    # größte Beträge zuerst: bei Budget-/Mengenlimit kommen die lukrativsten Funde dran
    todo.sort(key=lambda t: max(amounts(f"{t[0].title} {t[0].text}") or [0]), reverse=True)

    # Bedingungen bereits gemeldeter Deals regelmäßig neu prüfen (Update-Erkennung)
    recheck = [r for r in db.due_for_recheck(cfg["recheck_conditions_days"]) if r["deal_id"] in by_id
               and not any(t[0].deal_id == r["deal_id"] for t in todo)]

    # ---------- 3.–6. Bedingungen, LLM, Rechnung, Entscheidung ----------
    llm = None
    needs_llm = bool(todo) or bool(recheck)
    if needs_llm:
        def on_usage(purpose, i, o, w, c):
            db.record_usage(purpose, i, o, w, c)
        llm = (llm_factory or _default_llm)(cfg, today, on_usage)
    lcfg = cfg["llm"]
    candidates: list[AlertItem] = []

    for r in recheck:
        d = by_id[r["deal_id"]]
        c = load_conditions(fetcher, d, None, lcfg["max_conditions_chars"], False, r["conditions_url"])
        if c.status == "ok" and c.hash != r["alerted_conditions_hash"]:
            log.info("Bedingungen geändert: %s", d.title)
            todo.append((d, "update"))
        else:
            db.set_conditions(d.deal_id, r["conditions_url"], r["conditions_hash"])

    max_n = min(limit or 10**9, lcfg["max_deals_per_run"])
    verified, max_verify = 0, int(lcfg.get("max_verifications_per_run", 8))
    from dealradar.llm import BudgetExceeded, FatalAPIError
    for idx, (d, kind) in enumerate(todo):
        if idx >= max_n:
            log.info("max_deals_per_run erreicht – %d Deals bleiben für den nächsten Lauf", len(todo) - idx)
            break
        try:
            row = db.get(d.deal_id)
            max_chars = getattr(llm, "max_conditions_chars", lcfg["max_conditions_chars"])
            cond = load_conditions(fetcher, d, llm, max_chars,
                                   lcfg.get("find_conditions_with_web_search", True) and getattr(llm, "search_upfront", True),
                                   row["conditions_url"] if row else None)
            a = with_scan(check_frist(sanitize(llm.analyze(d, cond)), d.text, cond.text), cond)
            calc = compute(a, cfg["calc"])
            dec = decide(a, calc, cfg.raw, today)
            # Vielversprechend, aber ohne Originalbedingungen -> Bedingungen suchen und neu prüfen
            if (not a.bedingungen_gelesen and getattr(llm, "can_search", False) and verified < max_verify
                    and calc.netto_eur >= float(lcfg.get("verify_min_factor", 0.5)) * cfg["min_net_gain_eur"]
                    and not dec.nicht_empfohlen):
                verified += 1
                cond2 = verify_conditions(fetcher, llm, d, a.anbieter, max_chars)
                if cond2 is not None:
                    log.info("Originalbedingungen gefunden: %s", cond2.url)
                    cond = cond2
                    a = with_scan(check_frist(sanitize(llm.analyze(d, cond)), d.text, cond.text), cond)
                    calc = compute(a, cfg["calc"])
                    dec = decide(a, calc, cfg.raw, today)
            db.set_conditions(d.deal_id, cond.url, cond.hash)
            db.save_analysis(d.deal_id, d.content_hash, cond.hash, llm.model,
                             a.model_dump(), calc.to_dict(), dec.to_dict())
            s.analyzed += 1
            if dec.melden:
                if kind == "update":
                    prev = db.get(d.deal_id)
                    if prev["alerted_conditions_hash"] == cond.hash and prev["alerted_content_hash"] == d.content_hash:
                        db.set_status(d.deal_id, "analyzed", "unverändert seit letzter Meldung")
                        continue
                candidates.append(AlertItem(d.deal_id, kind, d.title, d.source, d.url, cond.url,
                                            cond.status, a, calc, dec))
                db.set_status(d.deal_id, "queued", dec.grund)
            else:
                db.set_status(d.deal_id, "analyzed", dec.grund)
                s.below.append((d.title, dec.grund))
        except FatalAPIError as e:
            log.error("%s – Lauf abgebrochen, Deals bleiben 'pending'", e)
            _warn(channels, db, "llm_fatal", "Analyse gestoppt (Modell/Key)", f"{e}\n\nOffene Deals bleiben für den nächsten Lauf.")
            s.fatal = True
            db.set_status(d.deal_id, "pending", "")
            s.errors.append(str(e))
            break
        except BudgetExceeded as e:
            log.warning("%s – restliche Deals bleiben 'pending' für den nächsten Lauf", e)
            s.budget_hit = True
            break
        except Exception as e:
            log.exception("Analyse fehlgeschlagen: %s", d.title)
            db.set_status(d.deal_id, "error", str(e)[:300])
            s.errors.append(f"{d.title[:60]}: {e}")
    if llm:
        s.llm_cost_eur = llm.spent_eur
    if s.budget_hit and s.analyzed == 0 and todo:
        # sonst wäre der Lauf "grün", aber stumm – Hinweis höchstens einmal pro Tag
        _warn(channels, db, "llm_quota_empty", "Heute nichts analysiert (Kontingent erschöpft)",
              f"Das LLM-Kontingent war aufgebraucht, bevor ein Deal analysiert werden konnte. "
              f"{len(todo)} Deals warten und kommen beim nächsten Lauf dran (Gemini-Gratisstufe "
              f"setzt um Mitternacht pazifischer Zeit zurück, 09:00 deutscher Zeit).")

    # ---------- 7. Benachrichtigung ----------
    candidates += _queued_from_db(db, {c.deal_id for c in candidates}, today, cfg["categories"])
    candidates.sort(key=lambda it: it.calc.netto_eur, reverse=True)
    slots = max(0, cfg["max_alerts_per_day"] - db.alerts_today())
    send, overflow = candidates[:slots], candidates[slots:]
    for it in overflow:
        log.info("Tageslimit erreicht, Meldung morgen: %s", it.title)
    if send:
        names = []
        for ch in channels:
            try:
                ch.send_alerts(send, today.isoformat())
                names.append(ch.name)
            except Exception as e:
                log.error("Kanal %s fehlgeschlagen: %s", getattr(ch, "name", ch), e)
                s.errors.append(f"Kanal {getattr(ch, 'name', ch)}: {e}")
        for it in send:
            db.record_alert(it.deal_id, it.kind, it.calc.netto_eur, names)
    else:
        log.info("Lauf %s: keine Treffer über der Schwelle (%d geholt, %d analysiert)",
                 today, s.fetched, s.analyzed)
        waiting = db.conn.execute("SELECT COUNT(*) FROM deals WHERE status='pending'").fetchone()[0]
        text = (f"Heute kein Angebot über der Schwelle von {cfg['min_net_gain_eur']:.0f} € netto.\n\n"
                f"- {s.fetched} Einträge geholt, {s.new} neu, {s.analyzed} analysiert\n"
                f"- {waiting} Deals warten noch auf die Analyse\n\n"
                f"Offene Treffer der letzten Tage: Issues mit Label `deal`.")
        for ch in channels:
            if hasattr(ch, "send_summary"):
                try:
                    ch.send_summary(today.isoformat(), text)
                except Exception as e:
                    log.error("Tagesbericht über %s fehlgeschlagen: %s", ch.name, e)
    s.alerts = send
    return s


def _queued_from_db(db: DB, exclude: set[str], today: date, cfg_categories: dict | None = None) -> list[AlertItem]:
    """Gestern wegen Tageslimit zurückgestellte Treffer erneut berücksichtigen."""
    out = []
    for r in db.conn.execute("SELECT * FROM deals WHERE status='queued'").fetchall():
        if r["deal_id"] in exclude:
            continue
        x = db.conn.execute("SELECT * FROM analyses WHERE deal_id=? ORDER BY id DESC LIMIT 1",
                            (r["deal_id"],)).fetchone()
        if not x:
            continue
        a = DealAnalysis.model_validate_json(x["analysis_json"])
        from dealradar.exclude import excluded
        if (topic := excluded(r["title"], a.anbieter)) or (cfg_categories is not None and not cfg_categories.get(a.kategorie, False)):
            db.set_status(r["deal_id"], "analyzed", f"ausgeschlossen ({topic or a.kategorie})")
            continue
        if a.frist and a.frist < today.isoformat():
            db.set_status(r["deal_id"], "analyzed", "Frist während Warteschlange abgelaufen")
            continue
        kind = "update" if r["alerted_content_hash"] else "neu"
        out.append(AlertItem(r["deal_id"], kind, r["title"], r["source"], r["url"], r["conditions_url"],
                             "ok" if r["conditions_hash"] else "nicht_gefunden", a,
                             CalcResult(**json.loads(x["calc_json"])),
                             Decision(**json.loads(x["decision_json"]))))
    return out


def with_scan(a, cond):
    """Klausel-Scanner auf dem vollständigen Bedingungstext: Lücken/Grauzonen + Pflichten mit Beleg."""
    from dealradar.clauses import merge, scan
    if cond.status != "ok":
        return a
    found, notes = scan(cond.full or cond.text)
    a.hebel = merge(a.hebel, found)
    a.unsicher += notes
    if cond.note:
        a.unsicher.append(f"Bedingungen {cond.note} – Klausel-Scanner hat den vollständigen Text geprüft.")
    return a


def verify_conditions(fetcher, llm, d: RawDeal, anbieter: str, max_chars: int):
    """Sucht die Originalbedingungen und übernimmt sie nur, wenn sie ladbar sind und den Anbieter nennen."""
    from dealradar.conditions import fetch_url, is_aggregator
    look = llm.find_conditions(d)
    if not look or not look.bedingungen_url or is_aggregator(look.bedingungen_url):
        if look:
            log.info("Keine Originalbedingungen für %s: %s", d.title[:60], look.hinweis)
        return None
    c = fetch_url(fetcher, look.bedingungen_url, max_chars)
    if c.status != "ok":
        log.info("Bedingungen nicht ladbar (%s): %s", c.status, c.note)
        return None
    names = {w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß0-9]{3,}", f"{anbieter} {look.anbieter}")}
    if names and not any(n in c.text.lower() for n in names):
        log.info("Gefundene Seite nennt den Anbieter nicht – verworfen: %s", c.url)
        return None
    return c


def _default_llm(cfg: Config, today: date, on_usage):
    provider = cfg["llm"].get("provider", "anthropic")
    if provider == "auto":            # Cloud: Gemini, danach Open-Source-Modell auf dem Runner
        import os
        from dealradar.llm_chain import ChainLLM
        from dealradar.llm_gemini import GeminiLLM
        from dealradar.llm_ollama import OllamaLLM
        chain = [("ollama", lambda: OllamaLLM(cfg["llm"], today.isoformat(), on_usage))]
        if os.environ.get("GEMINI_API_KEY"):
            chain.insert(0, ("gemini", lambda: GeminiLLM(cfg["llm"], today.isoformat(), on_usage)))
        return ChainLLM(chain)
    if provider == "gemini":
        from dealradar.llm_gemini import GeminiLLM
        return GeminiLLM(cfg["llm"], today.isoformat(), on_usage)
    if provider == "ollama":
        from dealradar.llm_ollama import OllamaLLM
        return OllamaLLM(cfg["llm"], today.isoformat(), on_usage)
    from dealradar.llm import LLM
    return LLM(cfg["llm"], today.isoformat(), on_usage)
