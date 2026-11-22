from dealradar.db import DB
from dealradar.models import RawDeal


def deal(text="EUR 100 Prämie", title="Bank - 100 €"):
    return RawDeal(source="s", source_key="k1", title=title, url="https://x/#k1", text=text)


def test_new_unchanged_changed(tmp_path):
    db = DB(tmp_path / "t.sqlite")
    assert db.upsert(deal(), "bank|100") == "neu"
    # noch nicht analysiert -> bleibt "neu" (wird beim nächsten Lauf nachgeholt)
    assert db.upsert(deal(), "bank|100") == "neu"
    db.set_status(deal().deal_id, "analyzed")
    assert db.upsert(deal(), "bank|100") == "unveraendert"
    # Whitespace/Formatierung ändert den Hash nicht
    assert db.upsert(deal(text="EUR   100   Prämie"), "bank|100") == "unveraendert"
    assert db.upsert(deal(text="EUR 150 Prämie"), "bank|150") == "geaendert"


def test_no_double_alert_but_update_after_change(tmp_path):
    db = DB(tmp_path / "t.sqlite")
    d = deal()
    db.upsert(d, "bank|100")
    db.record_alert(d.deal_id, "neu", 70.0, ["console"])
    assert db.upsert(d, "bank|100") == "unveraendert"      # nicht erneut melden
    assert db.was_alerted(d.deal_id)
    assert db.upsert(deal(text="EUR 200 Prämie"), "bank|200") == "geaendert"
    assert db.get(d.deal_id)["status"] == "pending"


def test_cross_source_duplicate(tmp_path):
    db = DB(tmp_path / "t.sqlite")
    a = RawDeal(source="a", source_key="x", title="Bank: 300 €", url="u", text="t")
    b = RawDeal(source="b", source_key="y", title="Bank - 300 €", url="u", text="t2")
    db.upsert(a, "bank|300")
    db.record_alert(a.deal_id, "neu", 100, [])
    db.upsert(b, "bank|300")
    assert db.offer_alerted_elsewhere("bank|300", b.deal_id)["deal_id"] == a.deal_id


def test_dry_run_copy_does_not_touch_disk(tmp_path):
    p = tmp_path / "t.sqlite"
    DB(p).upsert(deal(), "k")
    mem = DB(p, in_memory_copy=True)
    assert mem.get(deal().deal_id) is not None
    mem.upsert(deal(text="EUR 999"), "k")
    assert DB(p).get(deal().deal_id)["content_hash"] == deal().content_hash


def test_source_health(tmp_path):
    db = DB(tmp_path / "t.sqlite")
    db.record_source_run("s", True, 5)
    assert db.source_failing_since("s") is None
    db.record_source_run("s", False, 0, "boom")
    db.record_source_run("s", True, 0)
    assert db.source_failing_since("s") is not None
