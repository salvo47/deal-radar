"""Lokaler LLM-Schritt über Ollama (kostenlos, keine Daten verlassen den Mac).

Gleiche Schnittstelle wie dealradar.llm.LLM: analyze(), find_conditions(), model, spent_eur.
Die Ausgabe wird per JSON-Schema erzwungen (Ollama `format`) und mit Pydantic validiert.
Eine Websuche nach Bedingungen gibt es lokal nicht – nur direkte Links aus dem Fund."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Callable

import httpx
from pydantic import ValidationError

from dealradar.llm import SYSTEM_ANALYSE, BudgetExceeded, FatalAPIError
from dealradar.models import DealAnalysis, RawDeal

if TYPE_CHECKING:
    from dealradar.conditions import Conditions

log = logging.getLogger(__name__)

EXTRA_HINT = """
Antworte ausschließlich mit einem JSON-Objekt nach dem vorgegebenen Schema.
Zahlen als Zahlen (100.0, nicht "100 €"), unbekannte Werte als null. konfidenz zwischen 0.0 und 1.0."""


class OllamaLLM:
    def __init__(self, cfg: dict[str, Any], today: str,
                 on_usage: Callable[[str, int, int, int, float], None] | None = None,
                 client: httpx.Client | None = None) -> None:
        o = dict(cfg["ollama"])
        # Cloud-Lauf (GitHub Actions): kleineres Modell, weniger Kontext, Zeitbudget
        for k, env, typ in (("model", "DEALRADAR_OLLAMA_MODEL", str), ("num_ctx", "DEALRADAR_OLLAMA_NUM_CTX", int),
                            ("max_conditions_chars", "DEALRADAR_OLLAMA_MAX_CHARS", int),
                            ("max_minutes_per_run", "DEALRADAR_OLLAMA_MAX_MINUTES", float)):
            if os.environ.get(env):
                o[k] = typ(os.environ[env])
        self._t0 = time.monotonic()
        self.max_seconds = float(o.get("max_minutes_per_run", 0) or 0) * 60
        self.cfg, self.o, self.today, self.on_usage = cfg, o, today, on_usage
        self.spent_eur = 0.0
        self.max_conditions_chars = int(o.get("max_conditions_chars", 60000))
        self.client = client or httpx.Client(base_url=o.get("host", "http://localhost:11434"),
                                             timeout=float(o.get("timeout_seconds", 900)))
        try:
            tags = self.client.get("/api/tags").json()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Ollama nicht erreichbar unter {o.get('host')}: {e} – läuft die Ollama-App?") from e
        names = {m["name"] for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            raise RuntimeError(f"Ollama-Modell '{self.model}' nicht installiert. Vorhanden: {sorted(names)}. "
                               f"Installieren mit: ollama pull {self.model}")

    @property
    def model(self) -> str:
        return self.o["model"]

    def _chat(self, system: str, user: str) -> str:
        if self.max_seconds and time.monotonic() - self._t0 > self.max_seconds:
            raise BudgetExceeded(f"Zeitbudget für {self.model} ({self.max_seconds / 60:.0f} min) aufgebraucht")
        body = {
            "model": self.model, "stream": False, "keep_alive": "30m",
            "format": DealAnalysis.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": int(self.o.get("num_ctx", 32768))},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            r = self.client.post("/api/chat", json=body)
            r.raise_for_status()
        except httpx.ConnectError as e:
            raise FatalAPIError(f"Ollama nicht erreichbar: {e}") from e
        data = r.json()
        if self.on_usage:
            self.on_usage("analyse_lokal", data.get("prompt_eval_count", 0), data.get("eval_count", 0), 0, 0.0)
        if data.get("done_reason") == "length":
            raise RuntimeError("Antwort abgeschnitten (num_ctx zu klein?)")
        return data["message"]["content"]

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
            raw = self._chat(system, user if attempt == 0 else
                             user + f"\n\nDeine letzte Antwort war ungültig ({last_err}). Halte dich exakt an das Schema.")
            try:
                a = DealAnalysis.model_validate_json(raw)
            except (ValidationError, json.JSONDecodeError) as e:
                last_err = e
                log.warning("Ungültige Modellantwort (%s), Versuch %d", str(e)[:200], attempt + 1)
                continue
            # Ein lokales Modell darf nicht behaupten, Bedingungen gelesen zu haben, die nicht vorlagen
            if cond.status != "ok" and a.bedingungen_gelesen:
                a.bedingungen_gelesen = False
                a.unsicher.append("Modell meldete gelesene Bedingungen, obwohl keine vorlagen – korrigiert.")
            a.konfidenz = min(max(a.konfidenz, 0.0), 1.0)
            return a
        raise RuntimeError(f"Keine gültige strukturierte Antwort von {self.model}: {last_err}")

    def find_conditions(self, d: RawDeal):
        return None   # keine Websuche lokal
