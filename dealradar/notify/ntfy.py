"""Push über ntfy.sh (Topic aus .env). Pro Treffer eine kurze Nachricht."""
from __future__ import annotations

import httpx

from dealradar.notify import AlertItem


class Ntfy:
    name = "ntfy"

    def __init__(self, env: dict[str, str]) -> None:
        if not env.get("NTFY_TOPIC"):
            raise RuntimeError("ntfy-Kanal: NTFY_TOPIC fehlt in .env")
        self.topic = env["NTFY_TOPIC"]
        self.server = env.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

    def _post(self, title: str, message: str, click: str | None = None,
              priority: int = 3, tags: list[str] | None = None) -> None:
        payload = {"topic": self.topic, "title": title, "message": message,
                   "priority": priority, "tags": tags or []}
        if click:
            payload["click"] = click
        httpx.post(self.server, json=payload, timeout=20).raise_for_status()

    def send_alerts(self, items: list[AlertItem], run_date: str) -> None:
        for it in items:
            a = it.analysis
            msg = (f"{a.anbieter}: ~{it.calc.netto_eur:.0f} € netto\nFrist: {a.frist or a.frist_text or 'unbekannt'}\n"
                   f"Risiko: {a.risiko.rating}\nBedingungen selbst prüfen: {it.link}")
            self._post(("Update: " if it.kind == "update" else "") + it.title[:80], msg,
                       click=it.link, priority=4 if it.calc.netto_eur >= 100 else 3, tags=["moneybag"])

    def send_warning(self, title: str, text: str) -> None:
        self._post(f"Deal Radar: {title}", text, priority=3, tags=["warning"])
