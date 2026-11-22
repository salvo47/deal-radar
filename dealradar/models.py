"""Datenmodelle: Rohfund aus einer Quelle und strukturierte LLM-Analyse."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

AFFILIATE_HINTS = ("/r/", "/go/", "/visit/", "/track", "clickout", "linkcode=", "/out/", "/ext.php")


def norm_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


@dataclass
class RawDeal:
    """Ein normalisierter Eintrag aus einer Quelle (noch nicht analysiert)."""
    source: str
    source_key: str          # stabiler Schlüssel innerhalb der Quelle (Anker, GUID, Anbieter)
    title: str
    url: str
    text: str
    provider_hint: str = ""
    links: list[str] = field(default_factory=list)   # Links aus dem Eintrag (ohne Affiliate-Redirects)
    published: str = ""

    def __post_init__(self) -> None:
        self.title = norm_text(self.title)
        self.text = norm_text(self.text)
        self.links = [l for l in self.links if l.startswith("http") and not is_affiliate(l)]

    @property
    def deal_id(self) -> str:
        return hashlib.sha1(f"{self.source}|{self.source_key}".encode()).hexdigest()[:16]

    @property
    def content_hash(self) -> str:
        # Zahlen und Wörter, ohne Formatierung: Layoutänderungen lösen kein Update aus
        body = re.sub(r"[^\w€%,.\-]+", " ", f"{self.title} {self.text}".lower())
        return hashlib.sha256(body.encode()).hexdigest()


def is_affiliate(url: str) -> bool:
    u = url.lower()
    return any(h in u for h in AFFILIATE_HINTS)


# ---------- Strukturierte LLM-Ausgabe ----------

Kategorie = Literal["konto_praemie", "depot_praemie", "order_aktion", "neukundenzins",
                    "preisfehler", "cashback", "wechselbonus", "gutschein_praemie", "sonstiges"]
Rating = Literal["niedrig", "mittel", "hoch"]


class Kostenposten(BaseModel):
    beschreibung: str
    betrag_eur: float
    pro_ausfuehrung: bool = Field(description="true = fällt bei jeder Wiederholung an")


class OrderParameter(BaseModel):
    ordervolumen_min_eur: Optional[float] = Field(description="Mindestvolumen je Order laut Bedingungen, null wenn keins")
    ordergebuehr_eur: Optional[float] = Field(description="Ordergebühr je Ausführung, null wenn unbekannt")
    handelsplatzentgelt_eur: Optional[float]
    spread_prozent: Optional[float] = Field(description="nur wenn aus Quelle belegbar, sonst null")
    kauf_und_verkauf_zaehlen: bool = Field(description="zählen Kauf und Verkauf jeweils als eigene Order?")


class ZinsParameter(BaseModel):
    zinssatz_prozent: float
    betrag_max_eur: Optional[float]
    laufzeit_monate: float


class Klausel(BaseModel):
    zitat: str = Field(description="wörtliches Zitat aus den Bedingungen, keine Paraphrase")
    fundstelle: str = Field(description="Paragraph/Abschnitt/Seite, oder 'Quelle: Aggregator' wenn nicht aus Originalbedingungen")
    warum_interessant: str
    empfehlung: Literal["ok", "nicht_empfohlen"]
    grund_nicht_empfohlen: Optional[str]


class Risiko(BaseModel):
    rating: Rating
    begruendung: str
    rueckforderung: str
    kontosperre: str
    missbrauchsklausel: str
    haltefrist: str
    steuer: str


class Seriositaet(BaseModel):
    reguliert: Optional[bool]
    aufsicht: str = Field(description="z. B. BaFin, EZB, nationale EU-Aufsicht; 'unbekannt' wenn nicht belegt")
    einlagensicherung: str
    vorkasse_verlangt: bool
    krypto_ueberweisung_an_unbekannte: bool
    gewinnversprechen_ohne_bedingungen: bool
    bewertung: Literal["serioes", "unklar", "unserioes"]


class Kauf(BaseModel):
    """Aktionen mit Kaufpflicht (Geld-zurück, Gratis testen, Cashback auf einen Kauf, Preisfehler)."""
    kaufpreis_eur: float = Field(description="Preis, den man zahlen muss (inkl. Versand), je Ausführung")
    produkt_restwert_eur: Optional[float] = Field(
        default=None, description="realistischer Wiederverkaufswert des Produkts, nur wenn belegbar (z. B. "
                                  "Marktpreis gebraucht); sonst null – der Code setzt dann konservativ an")


class Hebel(BaseModel):
    """Legitimer Weg, mit derselben Aktion mehr herauszuholen."""
    art: Literal["cashback_portal", "geworben_praemie", "kombination", "wiederholung", "coupon_punkte",
                 "zahlungsart", "timing", "sonstiges"]
    beschreibung: str
    zusatz_eur: Optional[float] = Field(description="zusätzlicher Betrag in EUR, nur wenn belegt, sonst null")
    status: Literal["erlaubt", "nicht_ausgeschlossen", "unklar", "ausgeschlossen"] = Field(
        description="laut Bedingungen: ausdrücklich erlaubt / nicht ausgeschlossen / unklar / ausgeschlossen")
    beleg: str = Field(description="wörtliches Zitat oder Fundstelle, die den Status belegt; 'keine Angabe' wenn nichts dazu steht")


class DealAnalysis(BaseModel):
    ist_finanz_oder_sparaktion: bool
    kategorie: Kategorie
    anbieter: str
    region: list[Literal["DE", "AT", "EU", "andere"]]
    frist: Optional[str] = Field(description="letzter Teilnahmetag als YYYY-MM-DD, null wenn unbefristet/unklar")
    frist_text: str
    zielgruppe: str
    wohnsitz_erforderlich: list[str] = Field(description="Länder, in denen man wohnen MUSS (z. B. ['AT']); leer = keine Wohnsitzpflicht genannt")
    ausgeschlossen: list[str]
    praemie_art: Literal["bar", "gutschein", "sachpraemie", "zins", "cashback", "gebuehrenerlass", "ersparnis"]
    praemie_pro_ausfuehrung_eur: Optional[float] = Field(description="Prämie/Ersparnis in EUR je Ausführung bzw. einmalig; bei Zinsaktionen null")
    praemie_prozent_vom_volumen: Optional[float] = Field(
        default=None, description="Nur bei prozentualen Prämien (z. B. 0,75 % des Übertragsvolumens): der Prozentsatz; "
                                  "praemie_pro_ausfuehrung_eur ist dann die HÖCHSTprämie laut Bedingungen")
    wiederholbarkeit: Literal["einmalig", "mehrfach"]
    max_wiederholungen: Optional[int] = Field(description="Limit laut Bedingungen; null = kein Limit genannt")
    order: Optional[OrderParameter]
    zins: Optional[ZinsParameter]
    kauf: Optional[Kauf] = Field(default=None, description="nur bei Aktionen mit Kaufpflicht")
    hebel: list[Hebel] = Field(default_factory=list, description="legitime Wege, mehr herauszuholen")
    weitere_kosten: list[Kostenposten]
    kapitalbindung_eur: Optional[float]
    kapitalbindung_monate: Optional[float]
    zeitaufwand_minuten: float
    klauseln: list[Klausel]
    risiko: Risiko
    seriositaet: Seriositaet
    erfordert_unzulaessiges: bool = Field(description="true, wenn der Gewinn nur mit falschen Angaben, Mehrfachkonten unter falschen Daten, fremden Identitäten oder Umgehung der Identitätsprüfung erreichbar ist")
    unzulaessig_grund: Optional[str]
    bedingungen_gelesen: bool = Field(description="true nur, wenn Originalbedingungen des Anbieters vorlagen und gelesen wurden")
    konfidenz: float = Field(description="0.0 bis 1.0")
    unsicher: list[str] = Field(description="Was nicht sicher gelesen oder belegt werden konnte")
    zusammenfassung: str


class ConditionsLookup(BaseModel):
    anbieter: str
    anbieter_domain: Optional[str]
    bedingungen_url: Optional[str] = Field(description="offizielle Teilnahmebedingungen des Anbieters (HTML oder PDF), null wenn nicht gefunden")
    angebots_url: Optional[str]
    hinweis: str


_FIELD_ECHO = {"erfordert_unzulaessiges", "erforderlich_unzulaessiges", "unzulaessig", "nicht_empfohlen", "", "null", "none", "ja"}


def sanitize(a: DealAnalysis) -> DealAnalysis:
    """Fängt typische Schema-Fehler (v. a. kleiner lokaler Modelle) ab, bevor gerechnet wird.
    Ändert nichts an echten, begründeten Befunden."""
    note = a.unsicher.append
    # Zins-/Order-Blöcke ohne Inhalt entfernen
    if a.zins and (a.zins.zinssatz_prozent <= 0 or a.zins.laufzeit_monate <= 0):
        a.zins = None
    if a.order and a.kategorie not in ("order_aktion", "depot_praemie"):
        a.order = None
        note("Order-Parameter bei Nicht-Order-Aktion verworfen.")
    # "Unzulässig" nur gelten lassen, wenn es eine echte Begründung gibt
    def real(reason: str | None) -> bool:
        # "nicht empfohlen" ist laut Vorgabe nur für Täuschung/Identität/Mehrfachkonten gedacht
        r = (reason or "").strip().lower()
        return (r not in _FIELD_ECHO and len(r) >= 25 and
                bool(re.search(r"falsch|identit|mehrfach|mehrere konten|umgeh|täusch|taeusch|fremd|gefälscht|"
                               r"gefaelscht|scheinwohn|strohm|legitimation|missbrauch", r)))
    for k in a.klauseln:
        if k.empfehlung == "nicht_empfohlen" and not real(k.grund_nicht_empfohlen):
            k.empfehlung = "ok"
            k.grund_nicht_empfohlen = None
            note(f"Klausel ohne Begründung als 'nicht empfohlen' markiert – Markierung entfernt: „{k.zitat[:60]}…“")
    if a.erfordert_unzulaessiges and not real(a.unzulaessig_grund):
        a.erfordert_unzulaessiges = False
        a.unzulaessig_grund = None
        a.konfidenz = min(a.konfidenz, 0.4)
        note("Modell meldete 'erfordert Unzulässiges' ohne Begründung – ignoriert, Konfidenz gesenkt. Selbst prüfen.")
    # Zinsaktion als "sonstiges" einsortiert -> Neukundenzins (Kategorie 4)
    if a.kategorie == "sonstiges" and a.zins is not None:
        a.kategorie = "neukundenzins"
        note("Kategorie von 'sonstiges' auf 'neukundenzins' korrigiert (Zinsangebot erkannt).")
    # Hebel, die Täuschung/fremde Identitäten voraussetzen, werden nie gezeigt
    keep = []
    for h in a.hebel:
        if re.search(r"falsch|fake|fremde?n? (?:name|identit|daten)|zweitkont|mehrere konten|mehrfachkont|"
                     r"wegwerf|trash.?mail|scheinadress|umgeh|vpn|anderer (?:name|person)", h.beschreibung.lower()):
            note(f"Hebel verworfen (würde Täuschung/fremde Daten voraussetzen): „{h.beschreibung[:60]}…“")
            continue
        if h.status == "ausgeschlossen":
            h.zusatz_eur = None
        keep.append(h)
    a.hebel = keep
    if a.kauf and a.kauf.kaufpreis_eur <= 0:
        a.kauf = None
    if a.kategorie == "cashback" and a.kauf is None:
        a.konfidenz = min(a.konfidenz, 0.4)
        note("Kaufpreis nicht erfasst – Cashback auf einen Kauf lohnt nur, wenn du das Produkt ohnehin "
             "kaufen würdest; Rechnung ohne Kaufpreis ist zu optimistisch.")
    a.wohnsitz_erforderlich = [w.strip().upper()[:2] for w in a.wohnsitz_erforderlich if w.strip()]
    a.konfidenz = min(max(a.konfidenz, 0.0), 1.0)
    return a


_DATE_RX = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")


def dates_in(text: str) -> set[str]:
    out = set()
    for d, m, y in _DATE_RX.findall(text):
        y = int(y) + (2000 if len(y) == 2 else 0)
        try:
            out.add(f"{y:04d}-{int(m):02d}-{int(d):02d}")
        except ValueError:
            pass
    return out


def check_frist(a: DealAnalysis, *texts: str) -> DealAnalysis:
    """Die Frist muss als Datum in Quelle oder Bedingungen stehen – sonst Hinweis + Konfidenz runter.
    Schützt davor, dass ein falsch gelesenes Datum eine abgelaufene Aktion aktiv aussehen lässt."""
    if not a.frist:
        return a
    found = set().union(*(dates_in(t) for t in texts))
    if found and a.frist not in found:
        a.unsicher.append(f"Frist {a.frist} steht so nicht im Text (gefunden: {', '.join(sorted(found)[:6])}) – bitte selbst prüfen.")
        a.konfidenz = min(a.konfidenz, 0.5)
    return a
