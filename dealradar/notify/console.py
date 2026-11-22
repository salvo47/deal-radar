from __future__ import annotations

from dealradar.notify import DISCLAIMER, AlertItem


class Console:
    name = "console"

    def send_alerts(self, items: list[AlertItem], run_date: str) -> None:
        if not items:
            return
        print(f"\n===== Deal Radar {run_date}: {len(items)} Treffer =====")
        for i, it in enumerate(items, 1):
            a, c = it.analysis, it.calc
            print(f"\n[{i}] {it.short()}")
            print(f"    {it.title}")
            print(f"    Rechnung: brutto {c.brutto_eur:.2f} € - Kosten {c.kosten_eur:.2f} € = {c.netto_eur:.2f} €")
            for p in c.posten:
                print(f"      · {p}")
            for k in a.klauseln[:3]:
                print(f"    Klausel ({k.fundstelle}): „{k.zitat[:220]}“")
                print(f"      → {k.warum_interessant}{' [NICHT EMPFOHLEN]' if k.empfehlung == 'nicht_empfohlen' else ''}")
            print(f"    Risiko {a.risiko.rating}: {a.risiko.begruendung}")
            print(f"    Konfidenz {a.konfidenz:.2f}; Bedingungen gelesen: {'ja' if a.bedingungen_gelesen else 'NEIN'}")
            for h in it.decision.hinweise + a.unsicher:
                print(f"    ! {h}")
            print(f"    Bedingungen: {it.conditions_url or '— nicht geladen (' + it.conditions_status + ')'}")
            print(f"    Fundstelle:  {it.source_url}")
        print(f"\n{DISCLAIMER}\n")

    def send_warning(self, title: str, text: str) -> None:
        print(f"\n⚠️  {title}: {text}\n")
