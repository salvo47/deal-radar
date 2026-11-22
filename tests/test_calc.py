import pytest

from dealradar.calc import compute, interest_gain, spread_cost_per_execution
from dealradar.models import Kostenposten, OrderParameter, ZinsParameter
from conftest import CALC_CFG, base_analysis

CALC_CFG_SMALL = {**CALC_CFG, "verfuegbares_kapital_eur": 10000}


def test_einmalige_barpraemie(calc_cfg):
    r = compute(base_analysis(), calc_cfg)
    assert r.brutto_eur == 100.0
    assert r.kosten_eur == 20.0                 # 60 min x 20 €/h
    assert r.netto_eur == 80.0
    assert r.wiederholungen == 1 and not r.wiederholungen_geschaetzt


def test_gutschein_faktor(calc_cfg):
    r = compute(base_analysis(praemie_art="gutschein", zeitaufwand_minuten=0), calc_cfg)
    assert r.netto_eur == 90.0


def test_spread():
    assert spread_cost_per_execution(1000, 0.3, False) == pytest.approx(3.0)
    assert spread_cost_per_execution(1000, 0.3, True) == pytest.approx(1.5)


def test_order_praemie_pro_trade_spread_kleiner_als_praemie(calc_cfg):
    # Beispiel aus der Aufgabe: feste Prämie je Order, Spread-Verlust kleiner als Prämie
    a = base_analysis(kategorie="order_aktion", praemie_pro_ausfuehrung_eur=5.0,
                      wiederholbarkeit="mehrfach", max_wiederholungen=20, zeitaufwand_minuten=90,
                      order=OrderParameter(ordervolumen_min_eur=500, ordergebuehr_eur=1.0,
                                           handelsplatzentgelt_eur=None, spread_prozent=None,
                                           kauf_und_verkauf_zaehlen=True))
    r = compute(a, calc_cfg)
    # Prämie: 5 x (1-0,15) x 20 = 85 ; Kosten je Order: 1 + 0 + 500*0,3%/2 = 1,75 -> 35 ; Zeit 30
    assert r.brutto_eur == 85.0
    assert r.kosten_eur == 65.0
    assert r.netto_eur == 20.0


def test_kein_limit_wird_gedeckelt(calc_cfg):
    a = base_analysis(kategorie="order_aktion", praemie_pro_ausfuehrung_eur=2.0,
                      wiederholbarkeit="mehrfach", max_wiederholungen=None, zeitaufwand_minuten=0)
    r = compute(a, calc_cfg)
    assert r.wiederholungen == 10 and r.wiederholungen_geschaetzt
    assert r.brutto_eur == pytest.approx(17.0)


def test_spread_frisst_praemie(calc_cfg):
    a = base_analysis(kategorie="order_aktion", praemie_pro_ausfuehrung_eur=3.0,
                      wiederholbarkeit="mehrfach", max_wiederholungen=10, zeitaufwand_minuten=0,
                      order=OrderParameter(ordervolumen_min_eur=2000, ordergebuehr_eur=0,
                                           handelsplatzentgelt_eur=0, spread_prozent=0.3,
                                           kauf_und_verkauf_zaehlen=False))
    assert compute(a, calc_cfg).netto_eur < 0


def test_zins_ueber_referenz_nach_steuer(calc_cfg):
    assert interest_gain(4.0, 50000, 6, 2.0, False, 0) == pytest.approx(500.0)
    assert interest_gain(4.0, 50000, 6, 2.0, True, 26.375) == pytest.approx(368.125)
    assert interest_gain(1.5, 50000, 6, 2.0, False, 0) == 0.0
    a = base_analysis(kategorie="neukundenzins", praemie_art="zins", praemie_pro_ausfuehrung_eur=None,
                      zins=ZinsParameter(zinssatz_prozent=4.0, betrag_max_eur=50000, laufzeit_monate=6),
                      kapitalbindung_eur=50000, kapitalbindung_monate=6, zeitaufwand_minuten=30)
    r = compute(a, calc_cfg)
    assert r.netto_eur == pytest.approx(358.13, abs=0.01)   # keine doppelte Kapitalbindung


def test_kapitalbindung_und_weitere_kosten(calc_cfg):
    a = base_analysis(praemie_pro_ausfuehrung_eur=150, zeitaufwand_minuten=0,
                      kapitalbindung_eur=6000, kapitalbindung_monate=12,
                      weitere_kosten=[Kostenposten(beschreibung="Kontoführung 6 x 4,90", betrag_eur=29.4,
                                                   pro_ausfuehrung=False)])
    r = compute(a, calc_cfg)
    assert r.kosten_eur == pytest.approx(120 + 29.4)
    assert r.netto_eur == pytest.approx(0.6)


def test_zins_ohne_betrag(calc_cfg):
    a = base_analysis(praemie_pro_ausfuehrung_eur=None, zeitaufwand_minuten=0,
                      zins=ZinsParameter(zinssatz_prozent=4.0, betrag_max_eur=None, laufzeit_monate=6))
    calc_cfg["verfuegbares_kapital_eur"] = 10000
    r = compute(a, calc_cfg)
    assert r.brutto_eur == pytest.approx(73.63, abs=0.01)   # 10.000 € x 2 % x 0,5 J. nach Steuer
    assert any("verfügbares Kapital" in p for p in r.posten)


def test_zins_auf_verfuegbares_kapital_begrenzt(calc_cfg):
    calc_cfg["verfuegbares_kapital_eur"] = 10000
    a = base_analysis(kategorie="neukundenzins", praemie_art="zins", praemie_pro_ausfuehrung_eur=None,
                      zins=ZinsParameter(zinssatz_prozent=3.25, betrag_max_eur=250000, laufzeit_monate=6),
                      zeitaufwand_minuten=0)
    # 10.000 € x 1,25 % x 0,5 J. x (1 - 26,375 %) = 46,02 € (nicht 1.150 € auf 250.000 €)
    assert compute(a, calc_cfg).netto_eur == pytest.approx(46.02, abs=0.01)


def test_wiederholung_durch_kapital_begrenzt_und_keine_doppelzaehlung(calc_cfg):
    calc_cfg["verfuegbares_kapital_eur"] = 10000
    # Merkur-Fall: je 25.000 € Aufstockung ein Sonderzins-Festgeld, "kein Limit"
    a = base_analysis(kategorie="neukundenzins", praemie_art="zins", praemie_pro_ausfuehrung_eur=875,
                      wiederholbarkeit="mehrfach", max_wiederholungen=None, kapitalbindung_eur=25000,
                      kapitalbindung_monate=36, zeitaufwand_minuten=30,
                      zins=ZinsParameter(zinssatz_prozent=3.5, betrag_max_eur=25000, laufzeit_monate=36))
    r = compute(a, calc_cfg)
    assert r.wiederholungen == 0 and r.brutto_eur == 0 and r.netto_eur < 0


def test_prozentpraemie_mit_verfuegbarem_kapital():
    a = base_analysis(kategorie="depot_praemie", praemie_art="bar", praemie_pro_ausfuehrung_eur=5000.0,
                      praemie_prozent_vom_volumen=0.75, wiederholbarkeit="einmalig", kapitalbindung_eur=7000.0,
                      kapitalbindung_monate=12.0, zeitaufwand_minuten=0, order=None, zins=None, weitere_kosten=[])
    cfg = {**CALC_CFG_SMALL}
    r = compute(a, cfg)
    assert r.brutto_eur == 75.0                       # 0,75 % von 10.000 €, nicht 5.000 €


def test_prozentpraemie_kapital_reicht_nicht():
    a = base_analysis(kategorie="depot_praemie", praemie_art="bar", praemie_pro_ausfuehrung_eur=5000.0,
                      praemie_prozent_vom_volumen=0.75, wiederholbarkeit="einmalig", kapitalbindung_eur=20000.0,
                      kapitalbindung_monate=12.0, zeitaufwand_minuten=0, order=None, zins=None, weitere_kosten=[])
    r = compute(a, {**CALC_CFG_SMALL})
    assert r.brutto_eur == 0.0


def test_kauf_geld_zurueck_ohne_restwert():
    from dealradar.models import Kauf
    a = base_analysis(kategorie="cashback", praemie_art="cashback", praemie_pro_ausfuehrung_eur=40.0,
                      wiederholbarkeit="einmalig", kauf=Kauf(kaufpreis_eur=40.0), zeitaufwand_minuten=0,
                      order=None, zins=None, weitere_kosten=[], kapitalbindung_eur=None, kapitalbindung_monate=None)
    r = compute(a, CALC_CFG_SMALL)
    # 40 € zurück, 40 € bezahlt, Produkt mit 30 % angesetzt
    assert r.netto_eur == 12.0


def test_hebel_nicht_im_netto():
    from dealradar.models import Hebel
    a = base_analysis(hebel=[
        Hebel(art="cashback_portal", beschreibung="zusätzlich über Shoop", zusatz_eur=25.0,
              status="nicht_ausgeschlossen", beleg="keine Angabe"),
        Hebel(art="kombination", beschreibung="mit Aktion X", zusatz_eur=50.0, status="ausgeschlossen",
              beleg="nicht mit anderen Aktionen kombinierbar")])
    base = compute(base_analysis(), CALC_CFG_SMALL)
    r = compute(a, CALC_CFG_SMALL)
    assert r.netto_eur == base.netto_eur and r.zusatz_eur == 25.0


def test_ohne_limit_nicht_order_nur_einmal():
    a = base_analysis(kategorie="wechselbonus", praemie_art="cashback", praemie_pro_ausfuehrung_eur=80.0,
                      wiederholbarkeit="mehrfach", max_wiederholungen=None, order=None, zins=None,
                      weitere_kosten=[], kapitalbindung_eur=None, kapitalbindung_monate=None, zeitaufwand_minuten=0)
    r = compute(a, CALC_CFG_SMALL)
    assert r.wiederholungen == 1 and r.brutto_eur == 80.0
