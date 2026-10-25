from datetime import datetime, timedelta, timezone

import pytest

from dealfinder import db
from dealfinder.models import Classified, Listing, Scored

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def make_listing(source_id="1", source="ebay", price=100.0, end_time=None, title="RTX 4070"):
    return Listing(
        source=source,
        source_id=source_id,
        title=title,
        url=f"https://example.com/{source_id}",
        price=price,
        shipping=None,
        shipping_offered=True,
        image_urls=["https://img/1.jpg", "https://img/2.jpg"],
        is_auction=end_time is not None,
        end_time=end_time,
    )


def make_class(category="gpu", kind="single", key="rtx-4070"):
    return Classified(kind=kind, key=key, condition="used", quantity=1, category=category)


def make_scored(deal=True, target=False, rank=10.0):
    return Scored(
        total_cost=120.0,
        shipping_estimated=True,
        profit=40.0,
        discount_pct=0.25,
        is_target_hit=target,
        is_deal=deal,
        rank_score=rank,
    )


def add(conn, listing, c=None, scored=None, now=T0):
    return db.upsert_listing(conn, listing, c or make_class(), scored or make_scored(), now)


def keys(rows):
    return [r["source_id"] for r in rows]


def add_scan(conn, listing_id, rare=0, lot=0):
    conn.execute(
        "INSERT INTO scans (listing_id, status, model, items, is_rare_find, is_lot_find, cost_usd, scanned_at)"
        " VALUES (?, 'ok', 'm', '[]', ?, ?, 0.1, ?)",
        (listing_id, rare, lot, T0.isoformat()),
    )


def test_connect_creates_tables_and_pragmas(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "listings", "market_values", "comps", "item_prices", "scans", "vision_spend",
        "notifications", "product_overrides", "bulk_overrides", "platform_overrides",
        "custom_products", "custom_bulk_products", "runs", "settings",
    } <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    db.connect(tmp_path / "d.sqlite").close()  # idempotent


@pytest.mark.parametrize("source", ["hibid", "ebay"])
def test_snapshot_time_is_preserved_and_old_prices_do_not_replace_new(tmp_path, source):
    conn = db.connect(tmp_path / "snapshot.db")
    listing = make_listing(source=source, end_time=T0 + timedelta(days=1))
    listing.observed_at = T0
    lid = add(conn, listing, now=T0 + timedelta(minutes=10))
    assert db.get_listing(conn, lid)["last_seen"] == T0.isoformat()
    listing.price = 999
    listing.observed_at = T0 - timedelta(minutes=1)
    assert add(conn, listing, now=T0 + timedelta(minutes=11)) == lid
    assert db.get_listing(conn, lid)["price"] == 100


def test_expired_hibid_snapshot_is_hidden_even_when_auction_has_not_ended(tmp_path):
    conn = db.connect(tmp_path / "expired.db")
    listing = make_listing(source="hibid", end_time=T0 + timedelta(days=1))
    lid = add(conn, listing, c=make_class(kind="hardware_lot"))
    cutoff = T0 + timedelta(minutes=1)
    assert db.query_listings(conn, ["hardware_lot"], T0, hibid_before=cutoff) == []
    assert db.scan_candidates(conn, T0, hibid_before=cutoff) == []
    assert db.get_listing(conn, lid) is not None  # retained for history


def test_upsert_preserves_first_seen_and_dismissed(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    t1, t2 = T0, T0 + timedelta(hours=1)
    lid = add(conn, make_listing(price=100.0), now=t1)
    db.dismiss_listing(conn, lid)
    lid2 = add(conn, make_listing(price=80.0), now=t2)
    assert lid2 == lid
    row = db.get_listing(conn, lid)
    assert row["first_seen"] == t1.isoformat()
    assert row["dismissed"] == 1
    assert row["price"] == 80.0
    assert row["last_seen"] == t2.isoformat()
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 1
    assert db.get_listing(conn, 9999) is None


def test_upsert_stores_fields_and_converts_to_utc(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    tz = timezone(timedelta(hours=-5))
    end = datetime(2026, 10, 23, 7, 0, tzinfo=tz)  # 12:00 UTC
    lid = add(conn, make_listing(end_time=end), now=T0.astimezone(tz))
    row = db.get_listing(conn, lid)
    assert row["end_time"] == datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc).isoformat()
    assert row["first_seen"] == T0.isoformat()
    assert row["kind"] == "single" and row["key"] == "rtx-4070" and row["category"] == "gpu"
    assert row["image_urls"] == '["https://img/1.jpg", "https://img/2.jpg"]'
    assert row["shipping"] is None and row["shipping_estimated"] == 1
    assert row["total_cost"] == 120.0 and row["is_deal"] == 1


def test_upsert_without_score(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    lid = db.upsert_listing(conn, make_listing(), make_class(), None, T0)
    row = db.get_listing(conn, lid)
    assert row["total_cost"] is None and row["is_deal"] == 0 and row["rank_score"] == 0


def test_query_hides_ended_and_dismissed(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    add(conn, make_listing("ended", end_time=T0 - timedelta(minutes=1)))
    dismissed = add(conn, make_listing("dismissed", end_time=T0 + timedelta(hours=1)))
    db.dismiss_listing(conn, dismissed)
    add(conn, make_listing("live", end_time=T0 + timedelta(hours=1)))
    assert keys(db.query_listings(conn, ["single"], T0)) == ["live"]


def test_query_orders_target_hits_first(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    add(conn, make_listing("plain"), scored=make_scored(rank=100))
    add(conn, make_listing("target"), scored=make_scored(target=True, rank=1))
    add(conn, make_listing("mid"), scored=make_scored(rank=50))
    assert keys(db.query_listings(conn, ["single"], T0)) == ["target", "plain", "mid"]


def test_query_filters(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    add(conn, make_listing("gpu-ebay", end_time=T0 + timedelta(hours=3)), now=T0 - timedelta(days=2))
    add(
        conn,
        make_listing("cpu-sgw", source="shopgoodwill", end_time=T0 + timedelta(hours=30)),
        c=make_class("cpu", key="i7-13700k"),
        now=T0 - timedelta(hours=1),
    )
    add(conn, make_listing("gpu-bin"), now=T0 - timedelta(days=3))  # no end time
    q = lambda **kw: sorted(keys(db.query_listings(conn, ["single"], T0, **kw)))
    assert q() == ["cpu-sgw", "gpu-bin", "gpu-ebay"]
    assert q(category="cpu") == ["cpu-sgw"]
    assert q(source="ebay") == ["gpu-bin", "gpu-ebay"]
    assert q(ending_within_h=12) == ["gpu-ebay"]
    assert q(ending_within_h=48) == ["cpu-sgw", "gpu-ebay"]
    assert q(new_since=T0 - timedelta(days=1)) == ["cpu-sgw"]
    assert sorted(keys(db.query_listings(conn, ["hardware_lot"], T0))) == []


def test_query_platform_and_target_only(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    add(conn, make_listing("snes"), c=make_class("console", "game_lot", "snes"))
    add(conn, make_listing("n64"), c=make_class("console", "game_lot", "n64"), scored=make_scored(target=True))
    q = lambda **kw: sorted(keys(db.query_listings(conn, ["game_lot"], T0, **kw)))
    assert q(platform="snes") == ["snes"]
    assert q(target_only=True) == ["n64"]


def test_photo_finds(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    not_deal = add(conn, make_listing("find"), c=make_class("console", "game_lot", "snes"), scored=make_scored(deal=False))
    add_scan(conn, not_deal, rare=1)
    lot = add(conn, make_listing("lotfind"), c=make_class("console", "game_lot", "snes"), scored=make_scored(deal=True, rank=1))
    add_scan(conn, lot, lot=1)
    scanned_dull = add(conn, make_listing("dull"), c=make_class("console", "game_lot", "snes"), scored=make_scored(deal=True, rank=99))
    add_scan(conn, scanned_dull)
    add(conn, make_listing("unscanned"), c=make_class("console", "game_lot", "snes"), scored=make_scored(deal=False))
    add(conn, make_listing("deal"), c=make_class("console", "game_lot", "snes"), scored=make_scored(deal=True, rank=50))

    rows = db.query_listings(conn, ["game_lot"], T0)
    assert keys(rows) == ["find", "lotfind", "dull", "deal"]
    assert {r["source_id"]: r["photo_find"] for r in rows} == {"find": 1, "lotfind": 1, "dull": 0, "deal": 0}
    assert keys(db.query_listings(conn, ["game_lot"], T0, photo_finds_only=True)) == ["find", "lotfind"]
    assert len(db.query_listings(conn, ["game_lot"], T0, deals_only=False)) == 5


def test_settings_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    assert db.get_setting(conn, "scan_enabled") is None
    assert db.get_setting(conn, "scan_enabled", "false") == "false"
    db.set_setting(conn, "scan_enabled", "true")
    db.set_setting(conn, "scan_enabled", "false")
    assert db.get_setting(conn, "scan_enabled", "x") == "false"


def test_latest_runs_one_per_source(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    r1 = db.start_run(conn, "ebay", T0)
    db.finish_run(conn, r1, "error", 0, "boom", T0 + timedelta(minutes=1))
    r2 = db.start_run(conn, "ebay", T0 + timedelta(hours=1))
    db.finish_run(conn, r2, "ok", 12, None, T0 + timedelta(hours=1, minutes=1))
    r3 = db.start_run(conn, "hibid", T0)
    db.finish_run(conn, r3, "site_change", 0, "no items", T0 + timedelta(minutes=2))
    rows = {r["source"]: r for r in db.latest_runs(conn)}
    assert set(rows) == {"ebay", "hibid"}
    assert rows["ebay"]["id"] == r2 and rows["ebay"]["status"] == "ok"
    assert rows["ebay"]["listings_seen"] == 12 and rows["ebay"]["error"] is None
    assert rows["hibid"]["status"] == "site_change" and rows["hibid"]["error"] == "no items"


def test_market_value_and_comps_round_trip(tmp_path):
    from datetime import date

    from dealfinder.models import Comp, MarketValue

    conn = db.connect(tmp_path / "d.sqlite")
    mv = MarketValue(key="rtx-4070", condition="used", value=450.0, comp_count=2,
                     method="active_bin", low_confidence=True, computed_at=T0)
    comps = [Comp("RTX 4070 A", 440.0, date(2026, 10, 1), "https://e/1", "Pre-Owned"),
             Comp("RTX 4070 B", 460.0, None, "https://e/2", None)]
    db.save_market_value(conn, mv, comps)
    db.save_market_value(conn, MarketValue(**{**mv.__dict__, "condition": "new"}), comps[:1])
    assert db.get_market_value(conn, "rtx-4070", "used") == mv
    assert db.get_comps(conn, "rtx-4070", "used") == comps
    # a re-save replaces the comps for that key and condition only
    db.save_market_value(conn, mv, comps[1:])
    assert db.get_comps(conn, "rtx-4070", "used") == comps[1:]
    assert db.get_comps(conn, "rtx-4070", "new") == comps[:1]

    item = MarketValue(key="snes|chrono trigger", condition="cib", value=600.0, comp_count=3,
                       method="sold", low_confidence=False, computed_at=T0)
    db.save_market_value(conn, item, [], table="item_prices")
    row = conn.execute("SELECT platform, title, completeness FROM item_prices").fetchone()
    assert tuple(row) == ("snes", "chrono trigger", "cib")
    assert db.get_market_value(conn, "snes|chrono trigger", "cib", table="item_prices") == item


def test_stale_rows_hidden_and_not_scan_candidates(tmp_path):
    conn = db.connect(tmp_path / "d.sqlite")
    lot = make_class(category="console", kind="hardware_lot", key="ps2-console")
    add(conn, make_listing("stale"), lot, now=T0 - timedelta(hours=7))
    add(conn, make_listing("fresh"), lot, now=T0 - timedelta(hours=1))
    # An auction with a known end keeps its normal ended check, however long unseen.
    add(conn, make_listing("auction", end_time=T0 + timedelta(hours=5)), lot,
        now=T0 - timedelta(hours=7))
    cutoff = T0 - timedelta(hours=6)
    everything = ["auction", "fresh", "stale"]
    assert sorted(keys(db.query_listings(conn, ["hardware_lot"], T0))) == everything
    assert sorted(keys(db.query_listings(conn, ["hardware_lot"], T0, stale_before=cutoff))) == [
        "auction", "fresh"]
    assert sorted(keys(db.scan_candidates(conn, T0))) == everything
    assert sorted(keys(db.scan_candidates(conn, T0, stale_before=cutoff))) == ["auction", "fresh"]
