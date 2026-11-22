"""E-Mail über SMTP (Zugangsdaten nur aus .env)."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from dealradar.notify import DISCLAIMER, AlertItem
from dealradar.notify.render import deal_markdown

REQUIRED = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO")


class Email:
    name = "email"

    def __init__(self, env: dict[str, str], mode: str = "digest") -> None:
        missing = [k for k in REQUIRED if not env.get(k)]
        if missing:
            raise RuntimeError(f"E-Mail-Kanal: fehlende Variablen in .env: {', '.join(missing)}")
        self.env, self.mode = env, mode

    def _send(self, subject: str, body: str) -> None:
        e = self.env
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = e.get("MAIL_FROM") or e["SMTP_USER"]
        msg["To"] = e["MAIL_TO"]
        msg.set_content(body)
        port = int(e.get("SMTP_PORT", "587"))
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(e["SMTP_HOST"], port, context=ctx, timeout=30) as s:
                s.login(e["SMTP_USER"], e["SMTP_PASSWORD"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(e["SMTP_HOST"], port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(e["SMTP_USER"], e["SMTP_PASSWORD"])
                s.send_message(msg)

    def send_alerts(self, items: list[AlertItem], run_date: str) -> None:
        if not items:
            return
        if self.mode == "per_deal":
            for it in items:
                self._send(f"Deal Radar: {it.short()}", deal_markdown(it, "#"))
            return
        head = [f"Deal Radar {run_date} – {len(items)} Treffer", ""]
        head += [f"{i}. {it.short()}\n   {it.link}" for i, it in enumerate(items, 1)]
        body = "\n".join(head) + "\n\n" + "\n\n---\n\n".join(deal_markdown(it) for it in items)
        self._send(f"Deal Radar {run_date}: {len(items)} Treffer (bis {max(i.calc.netto_eur for i in items):.0f} €)",
                   body + f"\n\n{DISCLAIMER}\n")

    def send_warning(self, title: str, text: str) -> None:
        self._send(f"Deal Radar Warnung: {title}", text)
