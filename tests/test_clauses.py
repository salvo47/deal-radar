from dealradar.clauses import focus, merge, scan
from dealradar.models import Hebel

FILLER = "Die Bank haftet nach den gesetzlichen Vorschriften. Gerichtsstand ist Frankfurt am Main. " * 40

TERMS = (
    "§ 1 Aktion\nNeukunden erhalten eine Prämie von 100 Euro bei Eröffnung eines Girokontos.\n\n"
    "§ 2 Teilnahme\nAls Neukunde gilt, wer in den letzten 12 Monaten kein Girokonto bei der Bank hatte. "
    "Die Prämie wird nur einmal pro Person gewährt.\n\n" + FILLER +
    "\n\n§ 3 Rückforderung\nDie Prämie wird zurückgefordert, wenn das Konto innerhalb von 6 Monaten gekündigt wird.\n\n"
    "§ 4 Sonstiges\nDie Aktion ist mit anderen Aktionen kombinierbar."
)


def test_focus_keeps_relevant_clauses():
    short, cut = focus(TERMS, 900)
    assert cut and len(short) <= 1000
    assert "100 Euro" in short and "einmal pro Person" in short and "kombinierbar" in short
    assert "Gerichtsstand" not in short and "[…]" in short


def test_focus_short_text_untouched():
    assert focus("kurz", 100) == ("kurz", False)


def test_scan_finds_gaps_with_quotes():
    hebel, notes = scan(TERMS)
    by = {h.art: h for h in hebel}
    assert by["kombination"].status == "erlaubt" and "kombinierbar" in by["kombination"].beleg
    assert by["timing"].status == "erlaubt" and "12 Monaten" in by["timing"].beschreibung
    assert by["wiederholung"].status == "nicht_ausgeschlossen" and "eigenen, echten Daten" in by["wiederholung"].beschreibung
    assert by["cashback_portal"].status == "nicht_ausgeschlossen"
    assert any("Rückforderung" in n and "6 Monaten" in n for n in notes)


def test_scan_exclusions_win():
    t = ("Die Prämie gibt es einmal pro Haushalt. Die Aktion ist nicht mit anderen Aktionen kombinierbar. "
         "Abschlüsse über Cashback-Portale sind von der Prämie ausgeschlossen. " + FILLER)
    hebel, notes = scan(t)
    by = {h.art: h for h in hebel}
    assert by["kombination"].status == "ausgeschlossen"
    assert by["cashback_portal"].status == "ausgeschlossen"
    assert "wiederholung" not in by and any("Haushalt" in n or "haushalt" in n for n in notes)
    llm = [Hebel(art="cashback_portal", beschreibung="über Shoop", zusatz_eur=20, status="erlaubt", beleg="x")]
    m = merge(llm, hebel)
    cp = [h for h in m if h.art == "cashback_portal"]
    assert len(cp) == 1 and cp[0].status == "ausgeschlossen" and cp[0].zusatz_eur is None
