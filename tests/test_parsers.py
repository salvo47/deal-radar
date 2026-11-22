from dealradar.sources import load_module
from conftest import FIX


def parse(mod, fname, url):
    return load_module(mod).parse((FIX / fname).read_text(encoding="utf-8"), url, mod)


def test_modern_banking():
    ds = parse("modern_banking", "modern_banking.html", "https://www.modern-banking.de/brokerage-neukunden.htm")
    assert len(ds) == 3
    a = ds[0]
    assert a.provider_hint == "Testbank"
    assert a.url.endswith("brokerage-neukunden.htm#a2d")
    assert "EUR 1.000" in a.text
    assert a.links == []                       # Affiliate-Redirect /r/ wird verworfen
    assert ds[1].links == ["https://www.beispielbroker.de/aktion/teilnahmebedingungen.pdf"]
    assert ds[2].provider_hint == ""
    assert len({d.deal_id for d in ds}) == 3


def test_mydealz_rss():
    ds = parse("mydealz_rss", "mydealz.xml", "https://www.mydealz.de/rss/gruppe/konto-kreditkarten")
    assert [d.title for d in ds][0].startswith("50 € Bonus")
    assert ds[0].provider_hint == "Testbank"
    assert ds[0].links == ["https://www.testbank.de/bedingungen.pdf"]   # /visit/ entfernt
    assert ds[0].source_key == "https://www.mydealz.de/deals/testkonto-123"


def test_finanztip():
    ds = parse("finanztip", "finanztip.html", "https://www.finanztip.de/deals/")
    assert len(ds) == 1                        # abgelaufen + Nicht-Deal ignoriert
    d = ds[0]
    assert d.provider_hint == "Testbank"
    assert d.url == "https://www.finanztip.de/deals/#testbank-250-euro-bonus"
    assert "https://www.testbank.de/aktion" in d.links
    assert not any("facebook" in l or "track" in l for l in d.links)


def test_brokerexperte():
    ds = parse("brokerexperte", "brokerexperte.html", "https://www.brokerexperte.de/")
    assert [d.provider_hint for d in ds] == ["Testbroker", "Zinsbroker"]
    assert "zur Prämie" not in ds[0].title
    assert all("/go/" not in l for d in ds for l in d.links)


def test_depot_vergleichen():
    ds = parse("depot_vergleichen", "depot_vergleichen.html", "https://www.depot-vergleichen.de/neukundenaktionen/")
    assert [d.provider_hint for d in ds] == ["Testbroker", "Zweitbroker"]


def test_praemien_programme():
    ds = parse("praemien_programme", "praemien_programme.html", "https://www.praemien-programme.de/x.php")
    assert len(ds) == 2
    assert {d.text.split(".")[0] for d in ds} == {"Rubrik: Depots mit Bonus", "Rubrik: Girokonten mit Bonus"}
    assert ds[0].deal_id != ds[1].deal_id     # gleiche Bank, andere Rubrik


def test_structure_change_gives_zero():
    for mod in ["modern_banking", "finanztip", "brokerexperte", "depot_vergleichen", "praemien_programme"]:
        assert load_module(mod).parse("<html><body><p>neu gestaltet</p></body></html>", "https://x/", mod) == []
