from datetime import date

from dealradar.calc import CalcResult
from dealradar.models import Klausel
from dealradar.scoring import decide
from conftest import base_analysis

CFG = {"min_net_gain_eur": 30, "region": ["DE", "EU"], "risk_max": "mittel", "high_risk_multiplier": 5,
       "min_konfidenz": 0.3, "categories": {k: True for k in ["konto_praemie", "depot_praemie",
       "order_aktion", "neukundenzins", "preisfehler"]} | {"sonstiges": False}}


def calc(n):
    return CalcResult(n, 0, n, 1, False, [])


def test_ueber_schwelle():
    assert decide(base_analysis(), calc(31), CFG).melden


def test_unter_schwelle():
    d = decide(base_analysis(), calc(29.99), CFG)
    assert not d.melden and "unter Schwelle" in d.grund


def test_unzulaessig_wird_nie_gemeldet():
    d = decide(base_analysis(erfordert_unzulaessiges=True, unzulaessig_grund="Mehrfachkonten"), calc(9999), CFG)
    assert not d.melden and d.nicht_empfohlen


def test_unserioes_wird_nie_gemeldet():
    a = base_analysis()
    a.seriositaet.vorkasse_verlangt = True
    assert not decide(a, calc(9999), CFG).melden


def test_hohes_risiko_nur_ueber_5x():
    a = base_analysis()
    a.risiko.rating = "hoch"
    assert not decide(a, calc(149), CFG).melden
    d = decide(a, calc(151), CFG)
    assert d.melden and any("HOHES RISIKO" in h for h in d.hinweise)


def test_frist_abgelaufen():
    assert not decide(base_analysis(frist="2026-11-01"), calc(100), CFG, date(2026, 11, 21)).melden


def test_kategorie_und_region():
    assert not decide(base_analysis(kategorie="sonstiges"), calc(100), CFG).melden
    assert not decide(base_analysis(region=["andere"]), calc(100), CFG).melden
    assert decide(base_analysis(region=["AT"]), calc(100), CFG).melden


def test_ehrliche_hinweise():
    a = base_analysis(bedingungen_gelesen=False, konfidenz=0.5, klauseln=[Klausel(
        zitat="x", fundstelle="§1", warum_interessant="y", empfehlung="nicht_empfohlen",
        grund_nicht_empfohlen="falsche Angaben")])
    d = decide(a, calc(100), CFG)
    assert d.melden
    txt = " ".join(d.hinweise)
    assert "NICHT gelesen" in txt and "nicht empfohlen" in txt and "Konfidenz" in txt


def test_sanitize_drops_unfounded_flags():
    from dealradar.models import OrderParameter, ZinsParameter, sanitize
    a = base_analysis(erfordert_unzulaessiges=True, unzulaessig_grund="erfordert_unzulaessiges",
                      zins=ZinsParameter(zinssatz_prozent=0, betrag_max_eur=None, laufzeit_monate=0),
                      order=OrderParameter(ordervolumen_min_eur=700, ordergebuehr_eur=None,
                                           handelsplatzentgelt_eur=None, spread_prozent=None,
                                           kauf_und_verkauf_zaehlen=True),
                      klauseln=[Klausel(zitat="x", fundstelle="§1", warum_interessant="y",
                                        empfehlung="nicht_empfohlen", grund_nicht_empfohlen="erfordert_unzulaessiges")])
    a = sanitize(a)
    assert not a.erfordert_unzulaessiges and a.konfidenz <= 0.4
    assert a.zins is None and a.order is None and a.klauseln[0].empfehlung == "ok"


def test_sanitize_keeps_real_flag():
    from dealradar.models import sanitize
    a = sanitize(base_analysis(erfordert_unzulaessiges=True,
                               unzulaessig_grund="Prämie nur über mehrere Konten mit falschen Personendaten erreichbar"))
    assert a.erfordert_unzulaessiges


def test_check_frist():
    from dealradar.models import check_frist
    txt = "Antrag vom 14.10.26 bis zum 16.11.26, Konto bis 23.11.26 eröffnet."
    a = check_frist(base_analysis(frist="2026-11-26", konfidenz=0.9), txt)
    assert a.konfidenz == 0.5 and "selbst prüfen" in a.unsicher[-1]
    b = check_frist(base_analysis(frist="2026-11-16", konfidenz=0.9), txt)
    assert b.konfidenz == 0.9 and not b.unsicher


def test_wohnsitzpflicht():
    assert not decide(base_analysis(wohnsitz_erforderlich=["AT"]), calc(500), CFG | {"wohnsitz": "DE"}).melden
    assert decide(base_analysis(wohnsitz_erforderlich=["DE"]), calc(500), CFG | {"wohnsitz": "DE"}).melden


def test_sanitize_zins_kategorie():
    from dealradar.models import ZinsParameter, sanitize
    a = sanitize(base_analysis(kategorie="sonstiges", zins=ZinsParameter(zinssatz_prozent=4, betrag_max_eur=None, laufzeit_monate=4)))
    assert a.kategorie == "neukundenzins"


def test_nicht_empfohlen_nur_bei_taeuschung():
    from dealradar.models import sanitize
    a = sanitize(base_analysis(klauseln=[
        Klausel(zitat="4 % für Neukunden", fundstelle="Aggregator", warum_interessant="hoch",
                empfehlung="nicht_empfohlen", grund_nicht_empfohlen="Hoher Zinssatz, könnte sich bald ändern, Vorsicht"),
        Klausel(zitat="einmal je Person", fundstelle="§2", warum_interessant="x", empfehlung="nicht_empfohlen",
                grund_nicht_empfohlen="Mehrfache Teilnahme nur mit falschen Personendaten möglich")]))
    assert [k.empfehlung for k in a.klauseln] == ["ok", "nicht_empfohlen"]


def test_hebel_mit_taeuschung_wird_verworfen():
    from dealradar.models import Hebel, sanitize
    from conftest import base_analysis
    a = sanitize(base_analysis(hebel=[
        Hebel(art="wiederholung", beschreibung="Zweitkonto mit Wegwerf-Mail eröffnen", zusatz_eur=100.0,
              status="unklar", beleg="keine Angabe"),
        Hebel(art="cashback_portal", beschreibung="über Shoop abschließen", zusatz_eur=20.0,
              status="erlaubt", beleg="Cashback über Partnerportale möglich")]))
    assert [h.art for h in a.hebel] == ["cashback_portal"]
