"""Entscheidung: melden oder nicht. Harte Grenzen sind hier fest eingebaut."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from dealradar.calc import CalcResult
from dealradar.config import RISK_ORDER
from dealradar.models import DealAnalysis


@dataclass
class Decision:
    melden: bool
    grund: str
    hinweise: list[str] = field(default_factory=list)
    nicht_empfohlen: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(a: DealAnalysis, calc: CalcResult, cfg: dict[str, Any], today: date | None = None) -> Decision:
    today = today or date.today()
    hinweise: list[str] = []
    thr = float(cfg["min_net_gain_eur"])

    # --- Harte Grenzen ---
    if a.erfordert_unzulaessiges:
        return Decision(False, f"nicht empfohlen: {a.unzulaessig_grund or 'erfordert unzulässige Angaben/Umgehung'}",
                        nicht_empfohlen=True)
    if a.seriositaet.bewertung == "unserioes" or a.seriositaet.vorkasse_verlangt \
            or a.seriositaet.krypto_ueberweisung_an_unbekannte \
            or a.seriositaet.gewinnversprechen_ohne_bedingungen:
        return Decision(False, "Seriositätsprüfung nicht bestanden", nicht_empfohlen=True)
    if not a.ist_finanz_oder_sparaktion:
        return Decision(False, "keine Geld-/Sparaktion")

    # --- Filter ---
    if not cfg["categories"].get(a.kategorie, False):
        return Decision(False, f"Kategorie {a.kategorie} deaktiviert")
    regions = set(cfg["region"])
    if "EU" in regions:
        regions |= {"DE", "AT"}   # DE und AT sind EU-Länder
    if not set(a.region) & regions:
        return Decision(False, f"Region {a.region} nicht in {sorted(cfg['region'])}")
    wohnsitz = str(cfg.get("wohnsitz", "DE")).upper()
    if a.wohnsitz_erforderlich and wohnsitz not in a.wohnsitz_erforderlich:
        return Decision(False, f"Wohnsitz {'/'.join(a.wohnsitz_erforderlich)} erforderlich, deiner ist {wohnsitz}")
    if a.frist:
        try:
            if date.fromisoformat(a.frist) < today:
                return Decision(False, f"Frist abgelaufen ({a.frist})")
        except ValueError:
            hinweise.append(f"Frist nicht eindeutig lesbar: {a.frist_text}")
    if a.konfidenz < float(cfg["min_konfidenz"]):
        return Decision(False, f"Konfidenz {a.konfidenz:.2f} unter {cfg['min_konfidenz']}")
    if calc.netto_eur < thr:
        return Decision(False, f"Netto {calc.netto_eur:.2f} € unter Schwelle {thr:.0f} €")

    # --- Risiko ---
    risk, risk_max = RISK_ORDER[a.risiko.rating], RISK_ORDER[cfg["risk_max"]]
    if risk > risk_max:
        mult = float(cfg.get("high_risk_multiplier", 5))
        if calc.netto_eur > mult * thr:
            hinweise.append(f"⚠️ HOHES RISIKO – nur gemeldet, weil Netto > {mult:.0f}x Schwelle. {a.risiko.begruendung}")
        else:
            return Decision(False, f"Risiko {a.risiko.rating} über Maximum {cfg['risk_max']}")

    # --- Ehrliche Hinweise ---
    if not a.bedingungen_gelesen:
        hinweise.append("Originalbedingungen NICHT gelesen – Angaben nur aus der Quelle, bitte selbst prüfen.")
    if calc.wiederholungen_geschaetzt:
        hinweise.append(f"Kein Limit genannt – Rechnung mit {calc.wiederholungen} Wiederholungen angesetzt.")
    if any(k.empfehlung == "nicht_empfohlen" for k in a.klauseln):
        hinweise.append("Einzelne Klauseln als 'nicht empfohlen' markiert – nicht in die Rechnung eingeflossen.")
    if a.konfidenz < 0.6:
        hinweise.append(f"Niedrige Konfidenz ({a.konfidenz:.2f}).")
    return Decision(True, f"Netto {calc.netto_eur:.2f} € ≥ Schwelle {thr:.0f} €", hinweise)
