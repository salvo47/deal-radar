"""Klauseln aus Teilnahmebedingungen – ohne LLM.

1. focus(): kürzt lange Bedingungen auf die für Prämie/Pflichten/Ausschlüsse relevanten Absätze,
   damit auch kleine Open-Source-Modelle (wenig Kontext, CPU) sie lesen können.
2. scan(): sucht im VOLLEN Text deterministisch nach Lücken und Grauzonen, also nach dem, was die
   Bedingungen erlauben oder nicht ausschließen (Kombination, Cashback-Portal, Wiederholung,
   Rückkehr als Neukunde nach Sperrfrist, Empfehlungsprogramm) – jeweils mit wörtlichem Beleg.
   Nichts davon setzt falsche Angaben, fremde Identitäten oder Mehrfachkonten voraus.
"""
from __future__ import annotations

import re

from dealradar.models import Hebel

# Gewichte: was für Prämie, Pflichten und Ausschlüsse zählt
_KW = [
    (3, r"prämie|praemie|bonus|cashback|gutschrift|guthaben|startguthaben|geld zurück|erstattung|rabatt|gutschein"),
    (3, r"neukunde|bestandskunde|teilnahmeberechtigt|teilnahmevoraussetz|ausgeschlossen|nicht teilnahme"),
    (3, r"einmal|einmalig|je person|pro person|je haushalt|pro haushalt|je kunde|pro kunde|mehrfach"),
    (3, r"kombinier|nicht mit anderen|kumulier|portal|bonusprogramm|vermittler"),
    (2, r"mindest|innerhalb von|spätestens|bis zum|frist|zeitraum|aktionszeitraum|laufzeit|kündig"),
    (2, r"rückforder|zurückgefordert|rückbuch|verfällt|aberkannt|storn|widerruf"),
    (2, r"geldeingang|gehaltseingang|einzahlung|umsatz|transaktion|kartenzahlung|order|trade|sparplan"),
    (2, r"wohnsitz|volljährig|18 jahre|deutschland|österreich"),
    (1, r"auszahlung|ausgezahlt|überwiesen|gutgeschrieben|€|eur\b|prozent|%"),
]
_KW = [(w, re.compile(p, re.I)) for w, p in _KW]


def _units(text: str) -> list[str]:
    """Absätze; sehr lange Absätze in Sätze zerlegt."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n|\n(?=\s*(?:§|\d+\.|\(\d+\)|[a-z]\)|•|-)\s)", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= 700:
            out.append(para)
        else:
            out += [s.strip() for s in re.split(r"(?<=[.;!?])\s+(?=[A-ZÄÖÜ§(])", para) if s.strip()]
    return out


def score(unit: str) -> int:
    return sum(w * min(len(rx.findall(unit)), 3) for w, rx in _KW)


def focus(text: str, max_chars: int) -> tuple[str, bool]:
    """Gibt (text, gekürzt?) zurück. Relevante Absätze in Originalreihenfolge, Lücken als […]."""
    if len(text) <= max_chars:
        return text, False
    units = _units(text)
    ranked = sorted(range(len(units)), key=lambda i: (-score(units[i]), i))
    keep: set[int] = set()
    used = 0
    for i in ranked:
        if score(units[i]) <= 0:
            break
        n = len(units[i]) + 7
        if used + n > max_chars:
            continue
        keep.add(i)
        used += n
    parts, last = [], -1
    for i in sorted(keep):
        if i != last + 1:
            parts.append("[…]")
        parts.append(units[i])
        last = i
    if last != len(units) - 1:
        parts.append("[…]")
    return "\n\n".join(parts), True


# ---------------------------------------------------------------- Lücken / Grauzonen

def _find(rx: str, text: str) -> str | None:
    """Erster Satz, der das Muster enthält (als Beleg)."""
    m = re.search(rx, text, re.I | re.S)
    if not m:
        return None
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    end_dot = text.find(".", m.end())
    end = len(text) if end_dot == -1 else end_dot + 1
    s = re.sub(r"\s+", " ", text[start:end]).strip()
    return s[:300] + ("…" if len(s) > 300 else "")


_NO_COMBI = (r"nicht\s+(?:mit\s+)?(?:anderen|weiteren)\s+[^.]{0,60}?(?:kombinier|kumulier)|"
             r"(?:keine|nicht)\s+kombination|nicht\s+kombinierbar|nicht\s+kumulierbar|"
             r"(?:kombinier|kumulier)\w*\s+[^.]{0,40}?ausgeschlossen")
_YES_COMBI = r"(?<!nicht )(?:mit\s+(?:anderen|weiteren)\s+[^.]{0,60}?kombinierbar|kombinierbar\s+mit)"
_NO_PORTAL = (r"(?:cashback|bonus|prämien|vorteils|vergleichs)[-\s]?(?:portal|plattform|programm|seite)\w*"
              r"[^.]{0,120}?(?:ausgeschlossen|nicht\s+(?:berücksichtigt|teilnahme|gewährt|möglich)|keine)|"
              r"(?:über|via)\s+(?:dritte|vermittler|partner)[^.]{0,80}?(?:ausgeschlossen|kein\w*\s+(?:anspruch|prämie))")
_ONCE = r"(?:nur\s+)?(?:einmal(?:ig)?|ein\s+mal)\s+(?:pro|je)\s+(person|kunde|kundin|haushalt|konto|depot|karte|vertrag|adresse|iban)"
_COOLDOWN = (r"(?:in\s+den\s+(?:letzten|vergangenen)|innerhalb\s+der\s+(?:letzten|vergangenen)|seit\s+mindestens)\s+"
             r"(\d{1,2})\s+(monat|jahr)\w*[^.]{0,120}?(?:kunde|konto|depot|vertrag|karte|kein)")
_REFER = r"freunde\s+werben|kunden\s+werben\s+kunden|kwk\b|empfehlungs(?:programm|prämie|bonus)|weiterempfehl|werbeprämie|empfehlungscode|einladungscode"
_CLAWBACK = (r"(?:zurückgefordert|rückforder\w*|zurückbelast\w*|zurückgebucht|verrechnet)\s*[^.]{0,120}?"
             r"(?:innerhalb|vor\s+ablauf|binnen|wenn)[^.]{0,60}?(\d{1,2})\s+(monat|tag|woche)")
_MINHOLD = r"(?:mindestens|wenigstens)\s+(\d{1,2})\s+(monat|tag|woche)\w*[^.]{0,60}?(?:bestehen|halten|aktiv|ungekündigt|nicht\s+kündig)"


def scan(text: str) -> tuple[list[Hebel], list[str]]:
    """Lücken/Grauzonen als Hebel + Pflichten/Risiken als Hinweise. Arbeitet auf dem vollen Text."""
    hebel: list[Hebel] = []
    notes: list[str] = []
    if not text or len(text) < 200:
        return hebel, notes
    low = text.lower()

    # Kombination mit anderen Aktionen/Gutscheinen
    if (b := _find(_NO_COMBI, text)):
        hebel.append(Hebel(art="kombination", beschreibung="Kombination mit anderen Aktionen ist ausgeschlossen.",
                           zusatz_eur=None, status="ausgeschlossen", beleg=b))
    elif (b := _find(_YES_COMBI, text)):
        hebel.append(Hebel(art="kombination", beschreibung="Ausdrücklich mit anderen Aktionen kombinierbar – "
                           "parallel laufende Aktion/Gutschein desselben Anbieters mitnehmen.",
                           zusatz_eur=None, status="erlaubt", beleg=b))
    else:
        hebel.append(Hebel(art="kombination", beschreibung="Kein Kombinationsverbot gefunden – eine parallel "
                           "laufende Aktion oder ein Gutschein desselben Anbieters könnte zusätzlich gelten.",
                           zusatz_eur=None, status="nicht_ausgeschlossen",
                           beleg="keine Angabe (Klausel-Scanner: kein Verbot im vollständigen Text gefunden)"))

    # Abschluss über ein Cashback-Portal
    if (b := _find(_NO_PORTAL, text)):
        hebel.append(Hebel(art="cashback_portal", beschreibung="Abschluss über Cashback-/Vermittlerportale ist ausgeschlossen.",
                           zusatz_eur=None, status="ausgeschlossen", beleg=b))
    elif not re.search(r"portal|vermittler|über dritte", low):
        hebel.append(Hebel(art="cashback_portal", beschreibung="Portale/Vermittler werden nicht erwähnt – prüfe, ob ein "
                           "Cashback-Portal (z. B. Shoop, TopCashback) für diesen Abschluss zusätzlich zahlt.",
                           zusatz_eur=None, status="nicht_ausgeschlossen",
                           beleg="keine Angabe (Klausel-Scanner: Portale nicht erwähnt)"))

    # Wiederholung: Limit pro Person statt pro Haushalt
    m = re.search(_ONCE, text, re.I)
    if m:
        unit = m.group(1).lower()
        b = _find(_ONCE, text) or m.group(0)
        if unit in ("person", "kunde", "kundin") and "haushalt" not in low:
            hebel.append(Hebel(art="wiederholung", beschreibung="Limit gilt je Person, nicht je Haushalt – weitere "
                               "volljährige Personen im Haushalt können mit eigenem Konto und eigenen, echten Daten "
                               "selbst teilnehmen.", zusatz_eur=None, status="nicht_ausgeschlossen", beleg=b))
        else:
            notes.append(f"Klausel-Scanner: Teilnahme einmal je {unit} („{b[:120]}“).")

    # Rückkehr als Neukunde nach Sperrfrist (ehemalige Kunden sind nach X Monaten wieder berechtigt)
    m = re.search(_COOLDOWN, text, re.I)
    if m:
        n, u = m.group(1), "Monaten" if m.group(2).lower().startswith("monat") else "Jahren"
        hebel.append(Hebel(art="timing", beschreibung=f"Neukunde gilt, wer {n} {u} kein Kunde war – nach Ablauf der "
                           f"Sperrfrist ist eine erneute Teilnahme möglich (Kalendereintrag).",
                           zusatz_eur=None, status="erlaubt", beleg=_find(_COOLDOWN, text) or m.group(0)))

    # Empfehlungsprogramm (nur mit echter Empfehlung durch eine reale Person)
    if (b := _find(_REFER, text)):
        hebel.append(Hebel(art="geworben_praemie", beschreibung="Empfehlungsprogramm erwähnt – prüfe, ob Geworbene "
                           "zusätzlich eine Prämie bekommen, wenn eine echte Bekannte/ein echter Bekannter dich wirbt, "
                           "und ob das mit dieser Aktion kombinierbar ist.",
                           zusatz_eur=None, status="unklar", beleg=b))

    # Pflichten/Risiken
    if (b := _find(_CLAWBACK, text)):
        notes.append(f"Klausel-Scanner – Rückforderung möglich: „{b[:200]}“")
    if (b := _find(_MINHOLD, text)):
        notes.append(f"Klausel-Scanner – Mindesthaltedauer: „{b[:200]}“")
    return hebel, notes


def merge(existing: list[Hebel], found: list[Hebel]) -> list[Hebel]:
    """Scanner-Befunde ergänzen; ein Ausschluss aus dem Text schlägt eine LLM-Behauptung derselben Art."""
    out = list(existing)
    for f in found:
        same = [h for h in out if h.art == f.art]
        if not same:
            out.append(f)
        elif f.status == "ausgeschlossen":
            for h in same:
                if h.status in ("erlaubt", "nicht_ausgeschlossen"):
                    h.status, h.zusatz_eur = "ausgeschlossen", None
                    h.beleg = f"{f.beleg} (Klausel-Scanner)"
    return out
