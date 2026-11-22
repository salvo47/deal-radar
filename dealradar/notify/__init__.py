"""Benachrichtigungen. Jeder Kanal implementiert send_alerts(items, run_date) und send_warning(title, text)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dealradar.calc import CalcResult
from dealradar.models import DealAnalysis
from dealradar.scoring import Decision

DISCLAIMER = ("Hinweis, keine Anlage- oder Finanzberatung. Lies die Originalbedingungen vor dem "
              "Handeln selbst gegen. Eingesetztes Geld gilt als Risikokapital.")


@dataclass
class AlertItem:
    deal_id: str
    kind: str                 # "neu" | "update"
    title: str
    source: str
    source_url: str
    conditions_url: str | None
    conditions_status: str
    analysis: DealAnalysis
    calc: CalcResult
    decision: Decision

    @property
    def link(self) -> str:
        return self.conditions_url or self.source_url

    def short(self) -> str:
        a = self.analysis
        frist = a.frist or a.frist_text or "unbekannt"
        prefix = "UPDATE: " if self.kind == "update" else ""
        return (f"{prefix}{a.anbieter}: ~{self.calc.netto_eur:.0f} € netto | Frist {frist} | "
                f"Risiko {a.risiko.rating}")


def build_channels(names: list[str], cfg: Any, dry_run: bool) -> list[Any]:
    from dealradar.notify.console import Console
    chans: list[Any] = [Console()]
    if dry_run:
        return chans
    import logging
    log = logging.getLogger("dealradar")
    for n in names:
        try:
            if n == "email":
                from dealradar.notify.email import Email
                chans.append(Email(cfg.env, cfg["notify"].get("email_mode", "digest")))
            elif n == "ntfy":
                from dealradar.notify.ntfy import Ntfy
                chans.append(Ntfy(cfg.env))
            elif n == "github":
                from dealradar.notify.github_issue import GitHubIssue
                chans.append(GitHubIssue(cfg.env))
            elif n == "vault":
                from dealradar.notify.vault import Vault
                chans.append(Vault(cfg.vault_dir))
            else:
                raise ValueError(f"unbekannter Kanal: {n}")
        except RuntimeError as e:
            # ein nicht eingerichteter Kanal darf den Lauf nicht verhindern
            log.warning("Kanal %s übersprungen: %s", n, e)
    return chans
