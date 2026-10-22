import json
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from dealfinder import db
from dealfinder.budget import Budget
from dealfinder.catalog import Catalog
from dealfinder.config import Config
from dealfinder.models import (
    BulkProduct, Listing, MarketValue, Platform, Product, ScanItem, ScanResult,
)
from dealfinder.notify import Notifier
from dealfinder.pipeline import Pipeline, RefreshReport, ebay_queries, rotation_slice
from dealfinder.sources.base import SourceBlocked
from dealfinder.vision import ScanDeferred

T0 = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
WEBHOOK = "https://discord.com/api/webhooks/1/TOKEN"


def _plat(pid, match):
    return Platform(id=pid, name=pid, match=match, ebay_lot_query=f"{pid} game lot")


def _bulk(bid, category, match, **kw):
    return BulkProduct(id=bid, category=category, name=bid, ebay_query=f"{bid} q", match=match,
                       repair_cost=25, success_rate=0.6, **kw)


CATALOG = Catalog(
    products=[
        Product(id="4090", category="gpu", name="RTX 4090", ebay_query="rtx 4090",
                match=r"\b4090\b", max_price=700),
        Product(id="4070", category="gpu", name="RTX 4070", ebay_query="rtx 4070",
                match=r"\b4070\b"),
        Product(id="3dfx", category="gpu", name="Voodoo", ebay_query="voodoo",
                match=r"\bvoodoo\b", enabled=False),
    ],
    bulk=[
        _bulk("ps2-console", "console",
              r"\b(ps2|playstation 2)\b.*\b(consoles?|systems?|slim|fat|scph)\b"),
        _bulk("xbox-360-console", "console", r"\bxbox 360\b.*\b(consoles?|systems?)\b"),
        _bulk("4090-bulk", "gpu", r"\b4090\b", source_product_id="4090"),
        _bulk("off-bulk", "console", r"\boff\b", enabled=False),
    ],
    platforms=[
        _plat("snes", r"\b(snes|super nintendo( entertainment system)?)\b"),
        _plat("ps2", r"\b(ps2|playstation 2)\b"),
        _plat("nes", r"\b(nes|nintendo entertainment system)\b"),
        _plat("xbox-360", r"\bxbox 360\b"),
        Platform(id="n64", name="n64", match=r"\bn64\b", ebay_lot_query="rtx 4090",
                 enabled=True),  # duplicate query, de-duplicated
    ],
)


def listing(source_id, title, price=100.0, shipping=0.0, source="ebay", shipping_offered=True,
            end_time=None, images=None):
    return Listing(
        source=source, source_id=source_id, title=title, url=f"https://x/{source_id}",
        price=price, shipping=shipping, shipping_offered=shipping_offered,
        image_urls=images if images is not None else [f"https://img/{source_id}/0.jpg"],
        is_auction=end_time is not None, end_time=end_time,
    )


def mv(key, value, low=False):
    return MarketValue(key=key, condition="used", value=value, comp_count=9, method="sold",
                       low_confidence=low, computed_at=T0)


class FakeSource:
    def __init__(self, name, listings=(), error=None, by_query=None, photos=None):
        self.name = name
        self.listings = list(listings)
        self.by_query = by_query
        self.error = error
        self.extra_photos = photos
        self.queries: list[str] = []
        self.photo_calls: list[tuple[str, list[str]]] = []

    def search(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        if self.by_query is not None:
            return list(self.by_query.get(query, []))
        return list(self.listings)

    def photos(self, source_id, known):
        self.photo_calls.append((source_id, list(known)))
        return list(known) + (self.extra_photos or [])


class FakeMarket:
    def __init__(self, item_values=None):
        self.begin_runs = 0
        self.item_values = item_values or {}

    def begin_run(self):
        self.begin_runs += 1

    def product_value(self, p, condition):
        return mv(p.id, 1000.0)

    def bulk_value(self, b):
        return mv(b.id, 60.0)

    def per_game_value(self, platform):
        return mv(platform.id, 10.0)

    def item_price(self, platform, title, completeness):
        value = self.item_values.get(title)
        return None if value is None else mv(f"{platform}|{title}", value)


def scan_result(items=(), estimate=None, cost=0.20, status="ok"):
    return ScanResult(status=status, items=list(items), unit_count_estimate=estimate, notes="",
                      model="claude-opus-5-5", input_tokens=1000, output_tokens=100,
                      cost_usd=cost)


class FakeScanner:
    def __init__(self, result=None, delay=0.0, error=None):
        self.result = result or scan_result()
        self.delay = delay
        self.error = error
        self.calls: list[tuple[list[str], str]] = []

    def scan(self, image_urls, title):
        self.calls.append((list(image_urls), title))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "p.db")
    yield c
    c.close()


@pytest.fixture
def cfg():
    return Config(discord_webhook_url=WEBHOOK, broad_queries={"shopgoodwill": [], "hibid": []})


@pytest.fixture
def webhook():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(WEBHOOK).respond(204)


@pytest.fixture
def http():
    with httpx.Client() as client:
        yield client


def make_pipeline(conn, cfg, sources, *, scanner=None, market=None, http=None, catalog=CATALOG):
    budget = Budget(conn, cfg, lambda: T0)
    notifier = None
    if http is not None:
        notifier = Notifier(conn, cfg, http, sleep=lambda s: None, clock=lambda: T0)
    return Pipeline(conn, cfg, lambda: catalog, sources, market or FakeMarket(), scanner,
                    budget, notifier, lambda: T0)


def rows(conn):
    return {r["source_id"]: r for r in conn.execute("SELECT * FROM listings")}


def sent_payloads(route):
    return [json.loads(call.request.content) for call in route.calls]


# --- queries and rotation ----------------------------------------------------


def test_ebay_queries():
    assert ebay_queries(CATALOG) == [
        "rtx 4090", "rtx 4070",
        "ps2-console q lot", "xbox-360-console q lot",
        "snes game lot", "ps2 game lot", "nes game lot", "xbox-360 game lot",
    ]


def test_rotation_slice_near_equal_chunks():
    qs = list("abcdefgh")
    chunks = [rotation_slice(qs, i, 3) for i in range(3)]
    assert sum(chunks, []) == qs
    assert sorted(len(c) for c in chunks) == [2, 3, 3]
    assert rotation_slice(qs, 4, 3) == chunks[1]


def test_ebay_rotation_covers_all_queries(conn, cfg):
    ebay = FakeSource("ebay")
    p = make_pipeline(conn, cfg, {"ebay": ebay})
    for _ in range(6):  # ceil(3 h * 60 / 30 min) = 6 slices
        p.refresh()
    assert sorted(ebay.queries) == sorted(ebay_queries(CATALOG))
    assert len(ebay.queries) == len(ebay_queries(CATALOG))
    assert db.get_setting(conn, "ebay_rotation_index") == "6"


# --- refresh -----------------------------------------------------------------


def test_refresh_stores_and_scores(conn, cfg, http, webhook):
    ebay = FakeSource("ebay", [
        listing("single", "RTX 4090 Founders Edition 24GB", price=600),
        listing("games", "Lot of 20 SNES games", price=50, shipping=10),
        listing("pickup", "RTX 4090 Founders Edition", price=500, shipping=None,
                shipping_offered=False),
        listing("junk", "Kitchen blender", price=20),
    ])
    market = FakeMarket()
    p = make_pipeline(conn, cfg, {"ebay": ebay}, market=market, http=http)
    report = p.refresh()

    assert isinstance(report, RefreshReport)
    assert report.per_source == {"ebay": ("ok", 4)}
    assert market.begin_runs == 1
    stored = rows(conn)
    assert set(stored) == {"single", "games"}

    single = stored["single"]
    assert (single["kind"], single["key"], single["condition"]) == ("single", "4090", "used")
    assert single["total_cost"] == pytest.approx(600)
    assert single["profit"] == pytest.approx(1000 * 0.8675 - 20 - 600)
    assert single["is_target_hit"] == 1 and single["is_deal"] == 1

    games = stored["games"]
    assert (games["kind"], games["key"], games["quantity"]) == ("game_lot", "snes", 20)
    assert games["total_cost"] == pytest.approx(60)
    assert games["profit"] == pytest.approx(200 * 0.8675 - (8 + 0.4 * 20) - 60)
    assert games["is_deal"] == 1

    # target_hit and game_lot_deal are on by default; resale_deal is off.
    assert report.notified == 2
    titles = sorted(pl["embeds"][0]["title"] for pl in sent_payloads(webhook))
    assert titles == ["Lot of 20 SNES games", "RTX 4090 Founders Edition 24GB"]
    reasons = {r["reason"] for r in conn.execute("SELECT reason FROM notifications")}
    assert reasons == {"target_hit", "game_lot_deal"}

    p.refresh()
    assert market.begin_runs == 2
    assert webhook.call_count == 2  # each listing notifies at most once per reason


def test_source_error_isolated(conn, cfg):
    cfg.broad_queries = {"hibid": ["rtx"], "shopgoodwill": ["rtx"]}
    ebay = FakeSource("ebay", [listing("e1", "RTX 4090 Founders Edition 24GB", price=600)])
    hibid = FakeSource("hibid", error=SourceBlocked("blocked (HTTP 429)"))
    sgw = FakeSource("shopgoodwill", error=KeyError("items"))
    p = make_pipeline(conn, cfg, {"hibid": hibid, "shopgoodwill": sgw, "ebay": ebay})
    report = p.refresh()

    assert report.per_source["hibid"] == ("error", 0)
    assert report.per_source["shopgoodwill"] == ("error", 0)
    assert report.per_source["ebay"] == ("ok", 1)
    assert set(rows(conn)) == {"e1"}
    runs = {r["source"]: r for r in db.latest_runs(conn)}
    assert runs["hibid"]["status"] == "error"
    assert "blocked" in runs["hibid"]["error"]
    assert runs["shopgoodwill"]["status"] == "error"
    assert runs["ebay"]["status"] == "ok"
    assert runs["ebay"]["listings_seen"] == 1
    assert runs["ebay"]["finished_at"] is not None


def test_zero_parsed_is_site_change(conn, cfg):
    cfg.broad_queries = {"shopgoodwill": ["rtx", "ps5"], "hibid": []}
    sgw = FakeSource("shopgoodwill")
    hibid = FakeSource("hibid")
    p = make_pipeline(conn, cfg, {"shopgoodwill": sgw, "hibid": hibid})
    report = p.refresh()
    assert sgw.queries == ["rtx", "ps5"]
    assert report.per_source["shopgoodwill"] == ("site_change", 0)
    assert report.per_source["hibid"] == ("ok", 0)  # no queries is not a site change
    runs = {r["source"]: r["status"] for r in db.latest_runs(conn)}
    assert runs == {"shopgoodwill": "site_change", "hibid": "ok"}


# --- photo scanning ----------------------------------------------------------


def _three_game_lots():
    return [
        listing("cheap", "Lot of 20 SNES games", price=40),
        listing("mid", "Lot of 20 NES games", price=60),
        listing("dear", "Lot of 20 PS2 games", price=80),
    ]


def test_no_auto_scan_when_off(conn, cfg):
    scanner = FakeScanner()
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", _three_game_lots())},
                      scanner=scanner)
    report = p.refresh()
    assert scanner.calls == []
    assert report.scanned == 0
    assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0


def test_auto_scan_when_on_respects_cap(conn, cfg):
    cfg.scan_cost_ceiling = 0.25
    scanner = FakeScanner(scan_result(cost=0.20))
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", _three_game_lots())},
                      scanner=scanner)
    p.budget.set_scan_enabled(True)
    p.budget.set_caps(daily=0.60)
    report = p.refresh()

    assert report.scanned == 2
    assert [title for _, title in scanner.calls] == ["Lot of 20 SNES games",
                                                     "Lot of 20 NES games"]  # rank order
    scanned = {r["source_id"] for r in conn.execute(
        "SELECT l.source_id FROM scans s JOIN listings l ON l.id = s.listing_id")}
    assert scanned == {"cheap", "mid"}
    assert p.budget.spent_today() == pytest.approx(0.40)


def test_auto_scan_skips_over_max_scan_price(conn, cfg):
    scanner = FakeScanner()
    lots = [listing("big", "Lot of 100 SNES games", price=400),  # ranks first, costs > $300
            listing("small", "Lot of 20 SNES games", price=40)]
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", lots)}, scanner=scanner)
    p.budget.set_scan_enabled(True)
    p.refresh()
    assert [title for _, title in scanner.calls] == ["Lot of 20 SNES games"]


def test_no_scanner_skips_scanning(conn, cfg):
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", _three_game_lots())})
    p.budget.set_scan_enabled(True)
    assert p.refresh().scanned == 0
    lid = rows(conn)["cheap"]["id"]
    assert p.scan_listing(lid, manual=True) is None


def test_concurrent_scans_serialized(conn, cfg):
    cfg.scan_cost_ceiling = 0.25
    scanner = FakeScanner(scan_result(cost=0.20), delay=0.05)
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", _three_game_lots())},
                      scanner=scanner)
    p.refresh()  # scanning is off: stores rows only
    p.budget.set_caps(daily=0.30)  # room for one scan
    ids = [rows(conn)[sid]["id"] for sid in ("cheap", "mid")]
    results = {}

    def run(lid):
        results[lid] = p.scan_listing(lid, manual=True)

    threads = [threading.Thread(target=run, args=(lid,)) for lid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(scanner.calls) == 1
    assert sorted(r is None for r in results.values()) == [False, True]
    assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1


def test_manual_scan_when_off(conn, cfg):
    scanner = FakeScanner(scan_result(cost=0.20))
    ebay = FakeSource("ebay", [listing("g", "Lot of 20 SNES games", price=40,
                                       images=["https://img/a.jpg", "https://img/b.jpg"])],
                      photos=["https://img/detail.jpg"])
    p = make_pipeline(conn, cfg, {"ebay": ebay}, scanner=scanner)
    p.refresh()
    lid = rows(conn)["g"]["id"]
    assert p.budget.scan_enabled() is False

    assert p.scan_listing(lid, manual=False) is None  # auto scan obeys the switch
    assert scanner.calls == []

    result = p.scan_listing(lid, manual=True)
    assert result is not None and result.status == "ok"
    expected_urls = ["https://img/a.jpg", "https://img/b.jpg", "https://img/detail.jpg"]
    assert ebay.photo_calls == [("g", ["https://img/a.jpg", "https://img/b.jpg"])]
    assert scanner.calls == [(expected_urls, "Lot of 20 SNES games")]
    scan = db.get_scan(conn, lid)
    assert scan["status"] == "ok"
    assert json.loads(scan["photo_urls"]) == expected_urls
    assert scan["cost_usd"] == pytest.approx(0.20)
    assert p.budget.spent_today() == pytest.approx(0.20)

    assert p.scan_listing(lid, manual=True) is None  # already scanned
    assert len(scanner.calls) == 1


def test_scan_uses_stored_photos_without_source(conn, cfg):
    scanner = FakeScanner()
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", [
        listing("g", "Lot of 20 SNES games", images=["https://img/s.jpg"])])}, scanner=scanner)
    p.refresh()
    p.sources = {}
    assert p.scan_listing(rows(conn)["g"]["id"], manual=True) is not None
    assert scanner.calls[0][0] == ["https://img/s.jpg"]


def test_failed_scan_with_zero_cost_records_no_spend(conn, cfg):
    scanner = FakeScanner(scan_result(cost=0.0, status="failed"))
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", _three_game_lots())},
                      scanner=scanner)
    p.refresh()
    lid = rows(conn)["cheap"]["id"]
    assert p.scan_listing(lid, manual=True).status == "failed"
    assert db.get_scan(conn, lid)["status"] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM vision_spend").fetchone()[0] == 0


def test_scan_deferred_saves_nothing(conn, cfg):
    scanner = FakeScanner(error=ScanDeferred("rate limited"))
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", _three_game_lots())},
                      scanner=scanner)
    p.refresh()
    lid = rows(conn)["cheap"]["id"]
    assert p.scan_listing(lid, manual=True) is None
    assert db.get_scan(conn, lid) is None
    assert p.budget.spent_today() == 0
    assert not p.budget.lock.locked()


def test_scan_only_rescored_from_estimate(conn, cfg):
    ebay = FakeSource("ebay", [
        listing("games", "SNES games lot", price=40, shipping=5),
        listing("few", "NES games lot", price=40, shipping=5),
        listing("consoles", "PS2 consoles lot", price=40, shipping=5),
    ])
    p = make_pipeline(conn, cfg, {"ebay": ebay}, scanner=FakeScanner())
    p.refresh()
    before = rows(conn)
    assert {r["kind"] for r in before.values()} == {"scan_only"}
    assert all(r["total_cost"] is None for r in before.values())

    p.scanner = FakeScanner(scan_result(estimate=12))
    p.scan_listing(before["games"]["id"], manual=True)
    p.scanner = FakeScanner(scan_result(estimate=3))
    p.scan_listing(before["few"]["id"], manual=True)  # 3 < min_game_lot 5
    p.scanner = FakeScanner(scan_result(estimate=4))
    p.scan_listing(before["consoles"]["id"], manual=True)  # 4 >= min_hardware_lot 3

    def check(after):
        games = after["games"]
        assert (games["kind"], games["quantity"]) == ("game_lot", 12)
        assert games["total_cost"] == pytest.approx(45)
        assert games["profit"] == pytest.approx(120 * 0.8675 - (8 + 0.4 * 12) - 45)
        assert (after["few"]["kind"], after["few"]["quantity"]) == ("scan_only", None)
        consoles = after["consoles"]
        assert (consoles["kind"], consoles["quantity"]) == ("hardware_lot", 4)
        assert consoles["category"] == "console"
        assert consoles["total_cost"] == pytest.approx(45)
        unit_net = 60 * 0.8675 - 30
        assert consoles["profit"] == pytest.approx(4 * 0.6 * unit_net - 4 * 25 - 45)

    check(rows(conn))
    assert db.get_scan(conn, before["games"]["id"])["unit_count_estimate"] == 12
    p.refresh()  # the title still says "lot" with no count; the scan's estimate is kept
    check(rows(conn))


def test_photo_find_notifies_once(conn, cfg, http, webhook):
    items = [
        ScanItem(kind="game", platform="SNES", title="Chrono Trigger", edition=None,
                 completeness="loose", quantity=1, confidence="high", photo_index=0),
        ScanItem(kind="game", platform="SNES", title="Mario Kart", edition=None,
                 completeness="cib", quantity=1, confidence="high", photo_index=-1),
    ]
    market = FakeMarket(item_values={"Chrono Trigger": 180.0, "Mario Kart": 20.0})
    scanner = FakeScanner(scan_result(items))
    # 6 games at $10 do not make a text-only deal, so only the scan can notify.
    ebay = FakeSource("ebay", [listing("g", "Lot of 6 SNES games", price=100, shipping=10)])
    p = make_pipeline(conn, cfg, {"ebay": ebay}, scanner=scanner, market=market, http=http)
    p.budget.set_scan_enabled(True)

    first = p.refresh()
    second = p.refresh()
    assert len(scanner.calls) == 1
    assert (first.scanned, first.notified) == (1, 1)
    assert (second.scanned, second.notified) == (0, 0)
    assert webhook.call_count == 1
    description = sent_payloads(webhook)[0]["embeds"][0]["description"]
    assert "Chrono Trigger (SNES, loose) — $180" in description
    assert "Mario Kart" not in description
    scan = db.get_scan(conn, rows(conn)["g"]["id"])
    assert scan["is_rare_find"] == 1
    assert scan["identified_value"] == pytest.approx(200)
    stored = json.loads(scan["items"])
    assert [(i["title"], i["value"]) for i in stored] == [("Chrono Trigger", 180.0),
                                                          ("Mario Kart", 20.0)]


def test_lot_find_details_give_identified_value(conn, cfg, http, webhook):
    items = [ScanItem(kind="game", platform="NES", title=f"Game {n}", edition=None,
                      completeness="loose", quantity=1, confidence="high", photo_index=0)
             for n in range(6)]
    market = FakeMarket(item_values={f"Game {n}": 40.0 for n in range(6)})
    ebay = FakeSource("ebay", [listing("g", "Lot of 6 NES games", price=100, shipping=10)])
    p = make_pipeline(conn, cfg, {"ebay": ebay}, scanner=FakeScanner(scan_result(items)),
                      market=market, http=http)
    p.refresh()
    p.scan_listing(rows(conn)["g"]["id"], manual=True)
    assert webhook.call_count == 1
    description = sent_payloads(webhook)[0]["embeds"][0]["description"]
    assert "$240" in description and "Game 0" not in description


def test_ended_listing_not_scanned_or_notified(conn, cfg, http, webhook):
    ended = listing("old", "Lot of 20 SNES games", price=40, end_time=T0 - timedelta(hours=1))
    scanner = FakeScanner()
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", [ended])}, scanner=scanner,
                      http=http)
    p.budget.set_scan_enabled(True)
    report = p.refresh()
    stored = rows(conn)["old"]
    assert stored["is_deal"] == 1
    assert db.scan_candidates(conn, T0) == []
    assert scanner.calls == []
    assert (report.scanned, report.notified) == (0, 0)
    assert webhook.call_count == 0
    assert p.scan_listing(stored["id"], manual=True) is None
    assert scanner.calls == []


def test_scan_candidates(conn, cfg):
    lots = [*_three_game_lots(),
            listing("single", "RTX 4090 Founders Edition 24GB", price=600),
            listing("only", "SNES games lot", price=40)]
    p = make_pipeline(conn, cfg, {"ebay": FakeSource("ebay", lots)})
    p.refresh()
    ids = rows(conn)
    db.dismiss_listing(conn, ids["dear"]["id"])
    order = [r["source_id"] for r in db.scan_candidates(conn, T0)]
    assert order == ["cheap", "mid", "only"]
