"""Mehrere LLM-Anbieter nacheinander: erst Gemini (schnell, Gratiskontingent), danach ein
Open-Source-Modell über Ollama direkt auf dem GitHub-Runner (kein Key, kein Kontingent, nur Rechenzeit).

Ist der aktuelle Anbieter erschöpft (BudgetExceeded) oder kaputt (FatalAPIError), übernimmt der
nächste für den Rest des Laufs. Bedingungen werden dabei auf dessen Kontextgröße neu zugeschnitten."""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Callable

from dealradar.clauses import focus
from dealradar.llm import BudgetExceeded, FatalAPIError

log = logging.getLogger(__name__)


class ChainLLM:
    def __init__(self, factories: list[tuple[str, Callable[[], Any]]]) -> None:
        self.factories = list(factories)
        self.cur: Any = None
        self.cur_name = ""
        self.spent_eur = 0.0
        self.used: list[str] = []
        self._next()

    def _next(self, reason: str = "") -> None:
        if self.cur is not None:
            self.spent_eur += getattr(self.cur, "spent_eur", 0.0)
        while self.factories:
            name, make = self.factories.pop(0)
            try:
                self.cur, self.cur_name = make(), name
                self.used.append(name)
                if reason:
                    log.warning("LLM-Wechsel auf %s (%s)", name, reason[:200])
                return
            except Exception as e:             # z. B. kein Key, Ollama nicht installiert
                log.warning("LLM %s nicht nutzbar: %s", name, e)
        self.cur = None

    def _call(self, fn: str, *args):
        while self.cur is not None:
            try:
                return getattr(self.cur, fn)(*args)
            except (BudgetExceeded, FatalAPIError) as e:
                self._next(str(e))
                if self.cur is None:
                    raise
                if fn == "analyze":            # Bedingungen auf das neue Modell zuschneiden
                    d, cond = args
                    if cond.status == "ok":
                        txt, _ = focus(cond.full or cond.text, self.max_conditions_chars)
                        args = (d, replace(cond, text=txt))
        raise BudgetExceeded("kein LLM-Anbieter mehr verfügbar")

    def analyze(self, d, cond):
        return self._call("analyze", d, cond)

    def find_conditions(self, d):
        if not getattr(self.cur, "can_search", False):
            return None
        try:
            return self.cur.find_conditions(d)
        except (BudgetExceeded, FatalAPIError) as e:
            self._next(str(e))
            return None

    @property
    def model(self) -> str:
        return getattr(self.cur, "model", "-")

    @property
    def max_conditions_chars(self) -> int:
        return int(getattr(self.cur, "max_conditions_chars", 60000))

    @property
    def can_search(self) -> bool:
        return bool(getattr(self.cur, "can_search", False))

    search_upfront = False
