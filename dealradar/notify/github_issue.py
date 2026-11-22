"""Benachrichtigung als GitHub-Issue im eigenen (privaten) Repo.

Gedacht für den Cloud-Lauf: GitHub Actions stellt GITHUB_TOKEN und GITHUB_REPOSITORY bereit.
GitHub schickt neue Issues per E-Mail und als Push in der GitHub-App – ohne SMTP-Passwort.
Ein Issue pro Lauf mit Treffern (Label "deal"), Warnungen als eigenes Issue (Label "warnung")."""
from __future__ import annotations

import httpx

from dealradar.notify import DISCLAIMER, AlertItem
from dealradar.notify.render import deal_markdown

MAX_BODY = 60000          # GitHub-Limit 65536 Zeichen


class GitHubIssue:
    name = "github"

    def __init__(self, env: dict[str, str], client: httpx.Client | None = None) -> None:
        token, repo = env.get("GITHUB_TOKEN"), env.get("GITHUB_REPOSITORY")
        if not token or not repo:
            raise RuntimeError("GitHub-Kanal: GITHUB_TOKEN/GITHUB_REPOSITORY fehlen (nur in GitHub Actions gesetzt)")
        self.repo = repo
        self.client = client or httpx.Client(
            base_url="https://api.github.com", timeout=30,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "DealRadar"})

    def _issue(self, title: str, body: str, label: str) -> None:
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY] + "\n\n… (gekürzt, vollständig im Log-Artefakt des Laufs)"
        r = self.client.post(f"/repos/{self.repo}/issues", json={"title": title, "body": body, "labels": [label]})
        r.raise_for_status()

    def send_alerts(self, items: list[AlertItem], run_date: str) -> None:
        if not items:
            return
        head = [f"**{len(items)} Treffer** – {DISCLAIMER}", ""]
        head += [f"{i}. {it.short()} – [Bedingungen/Quelle]({it.link})" for i, it in enumerate(items, 1)]
        body = "\n".join(head) + "\n\n---\n\n" + "\n\n---\n\n".join(deal_markdown(it) for it in items)
        self._issue(f"Deal Radar {run_date}: {len(items)} Treffer (bis {max(i.calc.netto_eur for i in items):.0f} €)",
                    body + f"\n\n> {DISCLAIMER}\n", "deal")

    def send_warning(self, title: str, text: str) -> None:
        self._issue(f"Deal Radar Warnung: {title}", text, "warnung")
