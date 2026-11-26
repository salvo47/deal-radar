

def test_mydealz_trending_nur_mit_geld_zurueck():
    from dealradar.sources.mydealz_rss import _wanted
    t = "https://www.mydealz.de/rss/trending"
    assert _wanted(t, "[CB + Shoop] Ninja Creami für 141 €", "")
    assert _wanted(t, "Shoop x Samsung: 5% Cashback + 60€ Gutschein", "")
    assert not _wanted(t, "LEGO Technic Batman Tumbler für 117,37€", "gratis Versand")
    assert _wanted("https://www.mydealz.de/rss/gruppe/konto-kreditkarten", "Girokonto ohne Bonus", "")
