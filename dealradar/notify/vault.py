"""Markdown-Notiz pro Lauf im Obsidian-Vault. Schreibt NUR neue Dateien in den Deals-Ordner."""
from __future__ import annotations

from pathlib import Path

from dealradar.notify import DISCLAIMER, AlertItem
from dealradar.notify.render import deal_markdown


class Vault:
    name = "vault"

    def __init__(self, deals_dir: Path) -> None:
        self.dir = deals_dir
        if not self.dir.parent.exists():
            raise RuntimeError(f"Vault-Ordner {self.dir.parent} existiert nicht")

    def _new_path(self, run_date: str) -> Path:
        p = self.dir / f"{run_date}.md"
        n = 2
        while p.exists():                     # bestehende Dateien werden nie verändert
            p = self.dir / f"{run_date}-{n}.md"
            n += 1
        return p

    def send_alerts(self, items: list[AlertItem], run_date: str) -> Path | None:
        if not items:
            return None
        self.dir.mkdir(exist_ok=True)
        p = self._new_path(run_date)
        assert p.parent.resolve() == self.dir.resolve()
        fm = ["---", f"date: {run_date}", "tags: [deals]", f"treffer: {len(items)}", "---", "",
              f"# Deal Radar {run_date}", "", f"*{DISCLAIMER}*", ""]
        fm += [f"- {it.short()}" for it in items] + [""]
        body = "\n".join(fm) + "\n" + "\n\n".join(deal_markdown(it) for it in items)
        with p.open("x", encoding="utf-8") as f:   # "x": schlägt fehl, falls Datei existiert
            f.write(body)
        return p

    def send_warning(self, title: str, text: str) -> None:
        return None   # Warnungen gehen nur per Mail/Push, nicht in den Vault
