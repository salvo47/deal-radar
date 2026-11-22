"""Ende-zu-Ende mit Fake-Fetcher und Fake-LLM (keine Netz- oder API-Zugriffe)."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from dealradar.config import Config, ROOT
from dealradar.db import DB
from dealradar.http import Response
from dealradar.pipeline import amounts, run
from conftest import FIX, base_analysis

COND_TEXT = "§ 1 Teilnahme. " + "Die Prämie wird je Person einmal gewährt. " * 20


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, cache=True, delay=None):
        self.calls.append(url)
        if url in self.pages:
            return Response(url, 200, "text/html; charset=utf-8", self.pages[url].encode())
        return Response(url, 200, "text/html", f"<html><body><main>{COND_TEXT}</main></body></html>".encode())


class FakeLLM:
    model = "fake"

    def __init__(self, *a):
        self.spent_eur = 0.0
        self.n = 0

    def analyze(self, d, cond):
        self.n += 1
        top = max(amounts(d.title) or [0])
        return base_analysis(anbieter=d.provider_hint or "?", praemie_pro_ausfuehrung_eur=top,
                             bedingungen_gelesen=cond.status == "ok", zeitaufwand_minuten=60)

    def find_conditions(self, d):
        return None


def make_cfg(tmp: Path, sources: dict) -> Config:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())
    raw = copy.deepcopy(raw)
    raw["notify"]["channels"] = ["vault"]
    raw["notify"]["vault_dir"] = str(tmp / "vault" / "Deals")
    raw["paths"] = {"db": str(tmp / "db.sqlite"), "cache_dir": str(tmp / "cache"), "log_file": str(tmp / "log")}
    (tmp / "vault").mkdir()
    return Config(raw=raw, sources=sources, env={})


SOURCES = {
    "modern_banking": {"module": "modern_banking", "pages": ["https://mb/brokerage-neukunden.htm"], "cache": False},
    "praemien_programme": {"module": "praemien_programme", "pages": ["https://pp/x.php"]},
}
PAGES = {"https://mb/brokerage-neukunden.htm": (FIX / "modern_banking.html").read_text(),
         "https://pp/x.php": (FIX / "praemien_programme.html").read_text()}


def test_full_run_dedupe_and_vault(tmp_path):
    cfg = make_cfg(tmp_path, SOURCES)
    llm = FakeLLM()
    f = FakeFetcher(PAGES)
    s1 = run(cfg, llm_factory=lambda *a: llm, fetcher=f)
    # Testbank 300 € kommt in beiden Quellen vor -> nur einmal analysiert
    assert s1.duplicates == 1
    assert s1.prefiltered == 1                     # "Ohne Anker..." ohne Betrag
    titles = [a.title for a in s1.alerts]
    assert any("300" in t for t in titles) and any("150" in t for t in titles)
    notes = list((tmp_path / "vault" / "Deals").glob("*.md"))
    assert len(notes) == 1
    body = notes[0].read_text()
    assert body.startswith("---\ndate: ") and "tags: [deals]" in body
    assert "Habe ich gemacht" in body and "Ausgezahlt am" in body and "Risikokapital" in body

    # zweiter Lauf: nichts Neues -> keine Meldung, keine LLM-Analyse, keine neue Notiz
    n_before = llm.n
    s2 = run(cfg, llm_factory=lambda *a: llm, fetcher=FakeFetcher(PAGES))
    assert s2.alerts == [] and llm.n == n_before
    assert len(list((tmp_path / "vault" / "Deals").glob("*.md"))) == 1

    # Inhalt ändert sich -> Update
    pages = dict(PAGES)
    pages["https://mb/brokerage-neukunden.htm"] = PAGES["https://mb/brokerage-neukunden.htm"].replace(
        "EUR 150,00 Geldprämie", "EUR 250,00 Geldprämie")
    s3 = run(cfg, llm_factory=lambda *a: llm, fetcher=FakeFetcher(pages))
    assert [a.kind for a in s3.alerts] == ["update"]
    assert len(list((tmp_path / "vault" / "Deals").glob("*.md"))) == 2   # neue Datei, alte unverändert


def test_dry_run_writes_nothing(tmp_path):
    cfg = make_cfg(tmp_path, SOURCES)
    s = run(cfg, dry_run=True, llm_factory=lambda *a: FakeLLM(), fetcher=FakeFetcher(PAGES),
            db=DB(cfg.path("db"), in_memory_copy=True))
    assert s.alerts
    assert not (tmp_path / "vault" / "Deals").exists()
    assert not Path(cfg.path("db")).exists()


def test_max_alerts_per_day(tmp_path):
    cfg = make_cfg(tmp_path, SOURCES)
    cfg.raw["max_alerts_per_day"] = 1
    s = run(cfg, llm_factory=lambda *a: FakeLLM(), fetcher=FakeFetcher(PAGES))
    assert len(s.alerts) == 1 and s.alerts[0].calc.netto_eur == max(
        [s.alerts[0].calc.netto_eur])


def test_zero_results_warns(tmp_path, capsys):
    cfg = make_cfg(tmp_path, {"modern_banking": SOURCES["modern_banking"]})
    run(cfg, dry_run=True, llm_factory=lambda *a: FakeLLM(),
        fetcher=FakeFetcher({"https://mb/brokerage-neukunden.htm": "<html><body>leer</body></html>"}),
        db=DB(cfg.path("db"), in_memory_copy=True))
    assert "liefert 0 Einträge" in capsys.readouterr().out


def test_verify_conditions_nur_wenn_anbieter_genannt():
    from dealradar.models import ConditionsLookup, RawDeal
    from dealradar.pipeline import verify_conditions

    class SearchLLM:
        def find_conditions(self, d):
            return ConditionsLookup(anbieter="Musterbank", anbieter_domain="musterbank.de",
                                    bedingungen_url="https://musterbank.de/tnb", angebots_url=None, hinweis="")

    d = RawDeal("x", "k", "Musterbank 100 €", "https://agg/x", "100 € Prämie")
    ok_page = f"<html><body><main>Teilnahmebedingungen der Musterbank AG. {COND_TEXT}</main></body></html>"
    c = verify_conditions(FakeFetcher({"https://musterbank.de/tnb": ok_page}), SearchLLM(), d, "Musterbank", 60000)
    assert c is not None and c.status == "ok"
    # Seite ohne Anbieternamen wird verworfen
    assert verify_conditions(FakeFetcher({}), SearchLLM(), d, "Musterbank", 60000) is None


def test_quota_empty_warns(tmp_path, capsys):
    from dealradar.llm import BudgetExceeded

    class QuotaLLM(FakeLLM):
        def analyze(self, d, cond):
            raise BudgetExceeded("Kontingent erschöpft")

    cfg = make_cfg(tmp_path, SOURCES)
    s = run(cfg, dry_run=True, llm_factory=lambda *a: QuotaLLM(), fetcher=FakeFetcher(PAGES),
            db=DB(cfg.path("db"), in_memory_copy=True))
    assert s.budget_hit and s.analyzed == 0
    assert "Heute nichts analysiert" in capsys.readouterr().out


def test_chain_wechselt_bei_kontingent(tmp_path):
    from dealradar.llm import BudgetExceeded
    from dealradar.llm_chain import ChainLLM

    class Leer(FakeLLM):
        max_conditions_chars = 200000

        def analyze(self, d, cond):
            raise BudgetExceeded("leer")

    class Klein(FakeLLM):
        max_conditions_chars = 900

        def analyze(self, d, cond):
            self.seen = len(cond.text)
            return super().analyze(d, cond)

    klein = Klein()
    chain = ChainLLM([("gemini", Leer), ("ollama", lambda: klein)])
    cfg = make_cfg(tmp_path, SOURCES)
    s = run(cfg, dry_run=True, llm_factory=lambda *a: chain, fetcher=FakeFetcher(PAGES),
            db=DB(cfg.path("db"), in_memory_copy=True))
    assert s.analyzed > 0 and chain.used == ["gemini", "ollama"] and klein.seen <= 1000
