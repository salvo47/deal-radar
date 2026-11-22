"""LLM-Schritt über die Gemini API (kostenlose Stufe aus Google AI Studio).

Gedacht für den Cloud-Lauf (GitHub Actions), wo kein lokales Ollama läuft.
Gleiche Schnittstelle wie dealradar.llm.LLM: analyze(), find_conditions(), model, spent_eur.
Die Ausgabe wird per JSON-Schema erzwungen und mit Pydantic validiert.

Kontingent: Die kostenlose Stufe hat Limits pro Minute und pro Tag. Ein 429 beendet die
LLM-Schritte des Laufs sauber (wie das Budget bei Claude); offene Deals bleiben für morgen.
Der API-Key steht nur im Header, nie in der URL und nie im Log."""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Callable

import httpx
from pydantic import ValidationError

from dealradar.llm import SYSTEM_ANALYSE, BudgetExceeded, FatalAPIError
from dealradar.models import ConditionsLookup, DealAnalysis, RawDeal

if TYPE_CHECKING:
    from dealradar.conditions import Conditions

log = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com/v1beta"
EXTRA_HINT = """
Antworte ausschließlich mit einem JSON-Objekt nach dem vorgegebenen Schema.
Zahlen als Zahlen (100.0, nicht "100 €"), unbekannte Werte als null. konfidenz zwischen 0.0 und 1.0."""


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Löst $ref/$defs auf und entfernt 'title' – Gemini akzeptiert nur eine Teilmenge von JSON Schema."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


class ModelBusy(RuntimeError):
    """Modell dauerhaft überlastet (5xx nach Wiederholungen) – nächstes Modell probieren."""


class ModelUnavailable(ModelBusy):
    """Modell gibt 404 (für neue Keys abgeschaltet o. ä.)."""


class QuotaExhausted(ModelBusy):
    """Tageskontingent eines Modells aufgebraucht – andere Modelle haben eigene Kontingente."""


def quota_ids(body: str) -> str:
    try:
        return ", ".join(v.get("quotaId", "?") for d in json.loads(body)["error"].get("details", [])
                         for v in d.get("violations", [])) or "ohne Angabe"
    except (ValueError, KeyError, AttributeError):
        return body[:120]


def quota_info(body: str) -> tuple[str, float]:
    """('day'|'minute', Wartezeit in s) aus einer 429-Antwort der Gemini API."""
    kind, delay = "minute", 60.0
    try:
        for det in json.loads(body).get("error", {}).get("details", []):
            for v in det.get("violations", []):
                if "PerDay" in v.get("quotaId", ""):
                    kind = "day"
            if det.get("retryDelay"):
                delay = float(str(det["retryDelay"]).rstrip("s") or 60)
    except (ValueError, AttributeError):
        kind = "day" if "PerDay" in body else "minute"
    if delay > 3600:
        kind = "day"
    return kind, delay


class GeminiLLM:
    def __init__(self, cfg: dict[str, Any], today: str,
                 on_usage: Callable[[str, int, int, int, float], None] | None = None,
                 client: httpx.Client | None = None) -> None:
        g = cfg.get("gemini", {})
        self.cfg, self.g, self.today, self.on_usage = cfg, g, today, on_usage
        self.spent_eur = 0.0                     # kostenlose Stufe
        self.max_conditions_chars = int(g.get("max_conditions_chars", 200000))
        self.min_interval = float(g.get("min_interval_seconds", 7))
        self._last = 0.0
        key = os.environ.get("GEMINI_API_KEY", "")
        if client is None and not key:
            raise RuntimeError("GEMINI_API_KEY fehlt (.env bzw. GitHub-Secret)")
        self.client = client or httpx.Client(base_url=BASE, timeout=float(g.get("timeout_seconds", 300)),
                                             headers={"x-goog-api-key": key})
        self.schema = inline_schema(DealAnalysis.model_json_schema())
        self.exhausted: set[str] = set()         # Modelle mit aufgebrauchtem Tageskontingent / nicht verfügbar
        self.unavailable: set[str] = set()
        self.schema_in_prompt = False            # Rückfall, falls die API das Schema ablehnt

    @property
    def model(self) -> str:
        return self.g.get("model", "gemini-3.8-flash")

    @property
    def models_all(self) -> list[str]:
        return [self.g.get("model", "gemini-3.8-flash")] + list(self.g.get("fallback_models", []))

    @property
    def models(self) -> list[str]:
        ms = [self.model] + [m for m in self.g.get("fallback_models", []) if m != self.model]
        return [m for m in ms if m not in self.exhausted] or ms[-1:]

    def _post_any(self, body: dict[str, Any]) -> dict[str, Any]:
        """Erst das konfigurierte Modell, bei Überlastung die Ausweichmodelle."""
        models = self.models
        for i, m in enumerate(models):
            try:
                data = self._post(body, m)
                if m != self.model:       # funktionierendes Modell für den Rest des Laufs zuerst
                    log.info("Gemini: nutze ab jetzt %s", m)
                    self.g = {**self.g, "model": m}
                return data
            except ModelBusy as e:
                if isinstance(e, (QuotaExhausted, ModelUnavailable)):
                    self.exhausted.add(m)          # für den Rest des Laufs überspringen
                if isinstance(e, ModelUnavailable):
                    self.unavailable.add(m)
                if i == len(models) - 1:
                    if self.unavailable and self.unavailable >= set(self.models_all):
                        raise FatalAPIError(f"Kein konfiguriertes Gemini-Modell verfügbar – llm.gemini anpassen: {e}") from e
                    if self.exhausted:
                        raise BudgetExceeded(f"{e} – alle Ausweichmodelle ebenfalls erschöpft/überlastet/nicht verfügbar") from e
                    raise RuntimeError(str(e)) from e
                log.warning("%s – weiche auf %s aus", e, models[i + 1])
        raise RuntimeError("kein Gemini-Modell konfiguriert")

    def _post(self, body: dict[str, Any], model: str | None = None) -> dict[str, Any]:
        model = model or self.model
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        tries = max(1, int(self.g.get("attempts", 3)))
        for attempt in range(tries):
            self._last = time.monotonic()
            try:
                r = self.client.post(f"/models/{model}:generateContent", json=body)
            except httpx.TimeoutException as e:
                # langsames/hängendes Modell ist kein Netzproblem -> gleich nächstes Modell
                raise ModelBusy(f"Gemini {model} antwortet nicht ({type(e).__name__})") from e
            except httpx.TransportError as e:
                if attempt == tries - 1:
                    raise FatalAPIError(f"Gemini nicht erreichbar: {e}") from e
                time.sleep(10)
                continue
            if r.status_code == 429:
                kind, delay = quota_info(r.text)
                if kind == "day":
                    raise QuotaExhausted(f"Gemini-Tageskontingent erschöpft ({model})")
                if attempt == tries - 1:
                    raise ModelBusy(f"Gemini {model}: Minutenlimit hält an ({quota_ids(r.text)})")
                time.sleep(min(max(delay, 30.0), 65.0))   # Minutenfenster wirklich abwarten
                continue
            if r.status_code in (401, 403):
                raise FatalAPIError(f"Gemini-Key ungültig oder ohne Berechtigung (HTTP {r.status_code})")
            if r.status_code == 404:
                # einzelnes Modell nicht (mehr) verfügbar -> Ausweichmodell; fatal nur, wenn alle fehlen
                raise ModelUnavailable(f"Gemini-Modell '{model}' nicht verfügbar: {r.text[:150]}")
            if r.status_code >= 500:
                if attempt < 1:           # einmal kurz warten, dann nächstes Modell
                    time.sleep(5)
                    continue
                raise ModelBusy(f"Gemini {model} überlastet (HTTP {r.status_code})")
            if r.status_code >= 400:
                raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise RuntimeError("Gemini: keine Antwort nach 3 Versuchen")

    def _generate(self, system: str, user: str) -> str:
        gen: dict[str, Any] = {"temperature": 0, "responseMimeType": "application/json"}
        if self.schema_in_prompt:
            system += "\n\nJSON-Schema der Antwort:\n" + json.dumps(self.schema, ensure_ascii=False)
        else:
            gen["responseJsonSchema"] = self.schema
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": gen}
        try:
            data = self._post_any(body)
        except RuntimeError as e:
            if not self.schema_in_prompt and "HTTP 400" in str(e) and "schema" in str(e).lower():
                log.warning("Gemini lehnt das Schema ab – Schema wird stattdessen im Prompt übergeben")
                self.schema_in_prompt = True
                return self._generate(system, user)
            raise
        u = data.get("usageMetadata", {})
        if self.on_usage:
            self.on_usage("analyse_gemini", u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0), 0, 0.0)
        cands = data.get("candidates") or []
        if not cands:
            raise RuntimeError(f"Gemini ohne Antwort (blockiert? {data.get('promptFeedback')})")
        c = cands[0]
        if c.get("finishReason") == "MAX_TOKENS":
            raise RuntimeError("Gemini-Antwort abgeschnitten (MAX_TOKENS)")
        return "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))

    def analyze(self, d: RawDeal, cond: "Conditions") -> DealAnalysis:
        if cond.status == "ok":
            bed = f'<bedingungen url="{cond.url}">\n{cond.text}\n</bedingungen>'
        else:
            bed = (f'<bedingungen status="{cond.status}">Originalbedingungen nicht verfügbar: '
                   f"{cond.note}</bedingungen>")
        user = (f'<quelle name="{d.source}" url="{d.url}">\nTitel: {d.title}\n'
                f"Anbieter-Hinweis: {d.provider_hint}\n{d.text}\n</quelle>\n\n{bed}\n\n"
                "Analysiere diese Aktion gemäß Schema.")
        system = SYSTEM_ANALYSE.replace("{today}", self.today) + EXTRA_HINT
        last_err: Exception | None = None
        for attempt in range(2):        # ein Wiederholungsversuch bei ungültigem JSON
            raw = self._generate(system, user if attempt == 0 else
                                 user + f"\n\nDeine letzte Antwort war ungültig ({last_err}). Halte dich exakt an das Schema.")
            try:
                a = DealAnalysis.model_validate_json(raw)
            except (ValidationError, json.JSONDecodeError) as e:
                last_err = e
                log.warning("Ungültige Modellantwort (%s), Versuch %d", str(e)[:200], attempt + 1)
                continue
            if cond.status != "ok" and a.bedingungen_gelesen:
                a.bedingungen_gelesen = False
                a.unsicher.append("Modell meldete gelesene Bedingungen, obwohl keine vorlagen – korrigiert.")
            a.konfidenz = min(max(a.konfidenz, 0.0), 1.0)
            return a
        raise RuntimeError(f"Keine gültige strukturierte Antwort von {self.model}: {last_err}")

    # Suche läuft nicht vor jeder Analyse, sondern nur zur Prüfung vielversprechender Funde (spart Kontingent)
    search_upfront = False

    @property
    def can_search(self) -> bool:
        return bool(self.g.get("search_conditions", True))

    def find_conditions(self, d: RawDeal) -> ConditionsLookup | None:
        """Google-Suche (Grounding) nach den offiziellen Bedingungen. Eine URL wird nur übernommen,
        wenn ihre Domain unter den tatsächlich gefundenen Quellen ist (keine erfundenen Links)."""
        user = (f'<quelle name="{d.source}" url="{d.url}">\nTitel: {d.title}\n'
                f"Anbieter-Hinweis: {d.provider_hint}\n{d.text[:3000]}\n</quelle>\n\n"
                "Finde per Google-Suche die OFFIZIELLEN Teilnahmebedingungen dieser Aktion beim Anbieter "
                "(HTML oder PDF auf der Domain des Anbieters, keine Deal-/Vergleichsportale). "
                "Antworte nur in diesem Format:\nANBIETER: ...\nDOMAIN: ...\n"
                "BEDINGUNGEN_URL: https://... oder KEINE\nHINWEIS: ein Satz")
        body = {"contents": [{"role": "user", "parts": [{"text": user}]}],
                "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0}}
        try:
            data = self._post_any(body)
        except (BudgetExceeded, FatalAPIError):
            raise
        except Exception as e:
            log.warning("Bedingungs-Suche fehlgeschlagen für %s: %s", d.title[:60], e)
            return None
        c = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))
        found = {(ch.get("web", {}).get("title") or "").lower().removeprefix("www.")
                 for ch in c.get("groundingMetadata", {}).get("groundingChunks", [])}
        get = lambda k: (re.search(rf"^{k}:\s*(.+)$", text, re.M) or [None, ""])[1].strip()
        url = get("BEDINGUNGEN_URL")
        url = url if url.startswith("https://") else None
        hinweis = get("HINWEIS") or "keine Angabe"
        if url:
            host = urlsplit(url).hostname or ""
            host = host.removeprefix("www.")
            if not any(f and (host == f or host.endswith("." + f) or f.endswith("." + host)) for f in found):
                hinweis = f"vorgeschlagene URL {url} nicht durch Suchtreffer belegt – verworfen"
                url = None
        return ConditionsLookup(anbieter=get("ANBIETER") or d.provider_hint or "?",
                                anbieter_domain=get("DOMAIN") or None, bedingungen_url=url,
                                angebots_url=None, hinweis=hinweis)
