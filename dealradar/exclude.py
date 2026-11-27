"""Themen, die der Radar bewusst nicht meldet: Energie (Strom/Gas), Internet/Mobilfunk/TV und
Verträge mit Laufzeit (Versicherung, Abo, Streaming). Gemeldet werden nur Aktionen, die direkt Geld
bringen (Prämie, Bonus, Zins, Cashback) oder stark reduziert sind (Preisfehler, hoher Rabatt).
Geprüft wird nur der Titel/Anbieter, damit Bankbedingungen, die das Wort „Vertrag“ enthalten, nicht
versehentlich aussortiert werden."""
from __future__ import annotations

import re

_TOPICS = [
    ("Energie", r"\b(?:öko|oeko)?strom(?:tarif|vertrag|anbieter|wechsel|preis)?\b|\bgas(?:tarif|vertrag|anbieter)?\b|"
                r"\bstrom\s*(?:&|und|/)\s*gas\b|energie(?:anbieter|vertrag|tarif)|\b(?:e\.on|eon|vattenfall|yello|eprimo|"
                r"tibber|octopus energy|enbw|lichtblick|naturstrom|stadtwerke)\b"),
    ("Internet/Mobilfunk", r"\bdsl\b|glasfaser|kabel[- ]?internet|internet(?:tarif|vertrag|anschluss)|\bmobilfunk|"
                           r"handy(?:vertrag|tarif)|\btarif\b|allnet|sim[- ]?only|\bsim[- ]?karte|\b\d+\s?gb\b.*\b5g\b|"
                           r"magenta(?:mobil|zuhause|tv)|\b(?:vodafone|o2|1&1|congstar|freenet|otelo|klarmobil|"
                           r"winsim|mobilcom|telekom)\b"),
    ("Vertrag/Abo", r"versicherung|\bkfz\b|rechtsschutz|haftpflicht|\bvertrag\b|laufzeit|\babo\b|abonnement|"
                    r"\b(?:netflix|disney\+|sky|dazn|wow|rtl\+|paramount\+|spotify)(?!\w)|\bmtl\.|monatlich kündbar"),
]
_TOPICS = [(name, re.compile(rx, re.I)) for name, rx in _TOPICS]


def excluded(*texts: str) -> str | None:
    """Name des ausgeschlossenen Themas oder None."""
    blob = " ".join(t for t in texts if t)
    for name, rx in _TOPICS:
        if rx.search(blob):
            return name
    return None
