"""Gewinnrechnung – bewusst NICHT im LLM.

Das LLM liefert nur Zahlen aus den Bedingungen (DealAnalysis). Hier wird
konservativ gerechnet:

  brutto  = Prämie je Ausführung x Bewertungsfaktor (Bar 1.0, Gutschein, Sachprämie)
            x angesetzte Wiederholungen (Limit laut Bedingungen, sonst Obergrenze aus Config)
            [Order-Aktionen zusätzlich mit Sicherheitsabschlag]
          + Zinsvorteil über Referenzzins (nach Abgeltungsteuer, falls aktiviert)
  kosten  = Kosten je Ausführung (Ordergebühr, Handelsplatzentgelt, Spread) x Wiederholungen
          + einmalige Kosten + Zeitaufwand x Stundensatz
          + entgangene Zinsen auf gebundenes Kapital (Referenzzins)
  netto   = brutto - kosten

Anlagebeträge werden immer auf calc.verfuegbares_kapital_eur begrenzt (nicht die Obergrenze der Aktion).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from dealradar.models import DealAnalysis


@dataclass
class CalcResult:
    brutto_eur: float
    kosten_eur: float
    netto_eur: float
    wiederholungen: int
    wiederholungen_geschaetzt: bool
    posten: list[str] = field(default_factory=list)   # nachvollziehbare Rechenschritte
    zusatz_eur: float = 0.0                           # Hebel (erlaubt/nicht ausgeschlossen), NICHT im Netto
    zusatz_posten: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def spread_cost_per_execution(volume: float, spread_pct: float, buy_and_sell_count: bool) -> float:
    """Spread fällt einmal pro Kauf+Verkauf (Round Trip) an. Zählen Kauf und Verkauf
    jeweils als eigene Order, verteilt sich der Spread auf zwei Ausführungen."""
    round_trip = volume * spread_pct / 100.0
    return round_trip / 2.0 if buy_and_sell_count else round_trip


def interest_gain(rate_pct: float, amount: float, months: float, ref_rate_pct: float,
                  tax: bool, tax_pct: float) -> float:
    excess = max(0.0, rate_pct - ref_rate_pct)
    gain = amount * excess / 100.0 * months / 12.0
    if tax:
        gain *= 1 - tax_pct / 100.0
    return gain


def compute(a: DealAnalysis, c: dict[str, Any]) -> CalcResult:
    posten: list[str] = []
    brutto = kosten = 0.0

    # --- Wiederholungen ---
    if a.wiederholbarkeit == "einmalig":
        n, geschaetzt = 1, False
    elif a.max_wiederholungen is not None and a.max_wiederholungen > 0:
        n, geschaetzt = a.max_wiederholungen, False
    elif a.kategorie == "order_aktion":
        n, geschaetzt = int(c["max_wiederholungen_ohne_limit"]), True
        posten.append(f"Kein Limit genannt -> konservativ {n} Wiederholungen angesetzt")
    else:
        # Konto-, Wechsel-, Cashback-Prämien gelten praktisch einmal je Person/Haushalt/Lieferstelle
        n, geschaetzt = 1, False
        posten.append("Kein Limit genannt -> einmal angesetzt (Prämie je Person/Haushalt)")

    # --- Kapital: Wiederholungen nur so oft, wie das verfügbare Kapital reicht ---
    cap = float(c.get("verfuegbares_kapital_eur", 10000))
    if a.kapitalbindung_eur and a.kapitalbindung_eur > 0:
        by_cap = int(cap // a.kapitalbindung_eur)
        if by_cap < n:
            posten.append(f"Erfordert {a.kapitalbindung_eur:,.0f} € je Ausführung, verfügbar {cap:,.0f} € "
                          f"-> {by_cap} statt {n} Ausführung(en)")
            n, geschaetzt = by_cap, False

    # --- Prämie (bei Zinsaktionen steckt der Vorteil im Zinsblock, nicht doppelt zählen) ---
    if a.praemie_pro_ausfuehrung_eur and a.praemie_art == "zins" and a.zins:
        posten.append("Prämie ist der Zinsertrag selbst -> nur über den Zinsblock gerechnet")
    elif a.praemie_pro_ausfuehrung_eur:
        faktor = {"gutschein": c["gutschein_faktor"], "sachpraemie": c["sachpraemie_faktor"]}.get(
            a.praemie_art, 1.0)
        basis = a.praemie_pro_ausfuehrung_eur
        if a.praemie_prozent_vom_volumen:
            vol = cap if cap >= (a.kapitalbindung_eur or 0.0) else 0.0
            basis = min(basis, vol * a.praemie_prozent_vom_volumen / 100.0)
            posten.append(f"Prozentprämie {a.praemie_prozent_vom_volumen}% x {vol:,.0f} € (verfügbares Kapital), "
                          f"max. {a.praemie_pro_ausfuehrung_eur:,.0f} € -> {basis:.2f} €")
        p = basis * faktor
        if a.kategorie == "order_aktion":
            p *= 1 - c["sicherheitsabschlag_order_prozent"] / 100.0
        brutto += p * n
        posten.append(f"Prämie {basis:.2f} € x Faktor {faktor:.2f}"
                      + (f" x (1-{c['sicherheitsabschlag_order_prozent']}% Abschlag)" if a.kategorie == "order_aktion" else "")
                      + f" x {n} = {p * n:.2f} €")

    # --- Zinsen ---
    if a.zins and a.zins.zinssatz_prozent > 0 and n > 0:
        amount = min(a.zins.betrag_max_eur or a.kapitalbindung_eur or cap, cap)
        if not (a.zins.betrag_max_eur or a.kapitalbindung_eur):
            posten.append(f"Keine Obergrenze genannt -> verfügbares Kapital {cap:,.0f} € angesetzt")
        if amount <= 0:
            posten.append("Zinsaktion ohne belegten Anlagebetrag -> Zinsvorteil nicht angesetzt")
        else:
            g = interest_gain(a.zins.zinssatz_prozent, amount, a.zins.laufzeit_monate,
                              c["referenz_zins_prozent"], c["abgeltungsteuer_auf_zinsen"],
                              c["abgeltungsteuer_prozent"])
            brutto += g
            posten.append(f"Zinsvorteil ({a.zins.zinssatz_prozent}% - Referenz {c['referenz_zins_prozent']}%) "
                          f"x {amount:,.0f} € x {a.zins.laufzeit_monate} Mon."
                          + (" nach Abgeltungsteuer" if c["abgeltungsteuer_auf_zinsen"] else "")
                          + f" = {g:.2f} €")

    # --- Orderkosten je Ausführung ---
    if a.order:
        o = a.order
        vol = o.ordervolumen_min_eur or 0.0
        spread = o.spread_prozent if o.spread_prozent is not None else c["spread_prozent_default"]
        fee = o.ordergebuehr_eur or 0.0
        venue = o.handelsplatzentgelt_eur if o.handelsplatzentgelt_eur is not None else c["handelsplatzentgelt_eur_default"]
        sc = spread_cost_per_execution(vol, spread, o.kauf_und_verkauf_zaehlen)
        per = fee + venue + sc
        kosten += per * n
        posten.append(f"Je Order: Gebühr {fee:.2f} € + Handelsplatz {venue:.2f} € + Spread "
                      f"{spread:.2f}% auf {vol:,.0f} €{' (halbiert, Kauf und Verkauf zählen)' if o.kauf_und_verkauf_zaehlen else ''}"
                      f" = {per:.2f} € x {n} = {per * n:.2f} €")

    # --- Kaufpflicht: Kaufpreis als Kosten, Produkt nur mit (konservativem) Restwert ---
    if a.kauf and n > 0:
        k = a.kauf
        kosten += k.kaufpreis_eur * n
        if k.produkt_restwert_eur is not None:
            rest = min(k.produkt_restwert_eur, k.kaufpreis_eur * 3) * c.get("restwert_belegt_faktor", 0.8)
            herkunft = f"belegter Restwert {k.produkt_restwert_eur:.2f} € x {c.get('restwert_belegt_faktor', 0.8)}"
        else:
            rest = k.kaufpreis_eur * c.get("produkt_restwert_faktor", 0.3)
            herkunft = f"Kaufpreis x {c.get('produkt_restwert_faktor', 0.3)} (kein Restwert belegt)"
        brutto += rest * n
        posten.append(f"Kaufpreis {k.kaufpreis_eur:.2f} € x {n} = {k.kaufpreis_eur * n:.2f} € (Kosten); "
                      f"Produktwert {herkunft} = {rest * n:.2f} €")

    # --- weitere Kosten ---
    for k in a.weitere_kosten:
        betrag = k.betrag_eur * (n if k.pro_ausfuehrung else 1)
        kosten += betrag
        posten.append(f"{k.beschreibung}: {betrag:.2f} €")

    # --- Zeit ---
    zeit = a.zeitaufwand_minuten / 60.0 * c["stundensatz_eur"]
    kosten += zeit
    posten.append(f"Zeitaufwand {a.zeitaufwand_minuten:.0f} min x {c['stundensatz_eur']} €/h = {zeit:.2f} €")

    # --- Kapitalbindung (nicht doppelt bei Zinsaktionen, dort steckt der Referenzzins schon drin) ---
    if a.kapitalbindung_eur and a.kapitalbindung_monate and not (a.zins and a.zins.zinssatz_prozent > 0):
        bind = min(a.kapitalbindung_eur, float(c.get("verfuegbares_kapital_eur", 10000)))
        opp = bind * c["referenz_zins_prozent"] / 100.0 * a.kapitalbindung_monate / 12.0
        kosten += opp
        posten.append(f"Entgangene Zinsen auf {bind:,.0f} € für {a.kapitalbindung_monate} Mon. = {opp:.2f} €")

    netto = brutto - kosten
    posten.append(f"Netto = {brutto:.2f} € - {kosten:.2f} € = {netto:.2f} €")

    # --- Hebel: nur als Zusatzpotenzial, nicht im Netto (Bedingungen selbst prüfen) ---
    zusatz, zp = 0.0, []
    for h in a.hebel:
        if h.status in ("erlaubt", "nicht_ausgeschlossen") and h.zusatz_eur and h.zusatz_eur > 0:
            zusatz += h.zusatz_eur
            zp.append(f"{h.beschreibung}: +{h.zusatz_eur:.2f} € ({h.status.replace('_', ' ')})")
    return CalcResult(_r(brutto), _r(kosten), _r(netto), n, geschaetzt, posten, _r(zusatz), zp)
