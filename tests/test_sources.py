

def test_mydealz_trending_nur_mit_geld_zurueck():
    from dealradar.sources.mydealz_rss import _wanted
    t = "https://www.mydealz.de/rss/trending"
    assert _wanted(t, "[CB + Shoop] Ninja Creami für 141 €", "")
    assert _wanted(t, "Shoop x Samsung: 5% Cashback + 60€ Gutschein", "")
    assert not _wanted(t, "LEGO Technic Batman Tumbler für 117,37€", "gratis Versand")
    assert _wanted("https://www.mydealz.de/rss/gruppe/konto-kreditkarten", "Girokonto ohne Bonus", "")


def test_ausgeschlossene_themen():
    from dealradar.exclude import excluded
    assert excluded("E.ON & Shoop bis zu 80€ Cashback auf Strom- / Gasverträge") == "Energie"
    assert excluded("[Telekom Original] Apple iPhone 18 Pro Max + MagentaMobil S") == "Internet/Mobilfunk"
    assert excluded("Vodafone GigaZuhause 250 Kabel mit 150 € Wechselbonus") == "Internet/Mobilfunk"
    assert excluded("Disney+ bei Lidl Entertainment ab 4,99 € mtl.") == "Vertrag/Abo"
    assert excluded("HUK24 Kfz-Versicherung mit 50 € Prämie") is not None
    assert excluded("ING Girokonto: 150 € Neukundenprämie") is None
    assert excluded("[CB + Shoop] Ninja Creami für 141 €") is None
    assert excluded("Tepto Balkonkraftwerk mit 7 kWh Stromspeicher") is None


def test_trending_stark_reduziert():
    from dealradar.sources.mydealz_rss import _wanted
    t = "https://www.mydealz.de/rss/trending"
    assert _wanted(t, "50 % auf 7er Pack Tommy Hilfiger Trunks", "")
    assert _wanted(t, "ECCO Schuhe bis zu 60% reduziert", "")
    assert not _wanted(t, "MacBooks mit 12% Rabatt zum UVP", "")
