from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from dealfinder import db
from dealfinder.catalog import Catalog
from dealfinder.config import Config
from dealfinder.market import MarketService, iqr_median, label_condition
from dealfinder.models import BulkProduct, Comp, Listing, Platform, Product
from dealfinder.sources.base import SourceBlocked, SourceError

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

PRODUCTS = [
    Product(id="rtx-4070-ti", category="gpu", name="RTX 4070 Ti", ebay_query="rtx 4070 ti",
            match=r"\b(rtx )?4070 ti\b(?! super\b)"),
    Product(id="rtx-4070-ti-super", category="gpu", name="RTX 4070 Ti Super",
            ebay_query="rtx 4070 ti super", match=r"\b(rtx )?4070 ti super\b"),
]
PLATFORMS = [
    Platform(id="ps2", name="PS2", match=r"\b(ps2|playstation ?2)\b",
             ebay_lot_query="ps2 game lot"),
    Platform(id="snes", name="SNES", match=r"\b(snes|super nintendo)\b",
             ebay_lot_query="snes game lot"),
]
BULK = [
    BulkProduct(id="bulk-rtx-4070-ti", category="gpu", name="RTX 4070 Ti",
                ebay_query="rtx 4070 ti", match=PRODUCTS[0].match, repair_cost=40,
                success_rate=0.4, source_product_id="rtx-4070-ti"),
    BulkProduct(id="ps2-console", category="console", name="PS2 console",
                ebay_query="ps2 console",
                match=r"\b(ps2|playstation ?2)\b.*\b(consoles?|systems?|slim|fat|scph)\b",
                repair_cost=25, success_rate=0.6),
]
CATALOG = Catalog(products=PRODUCTS, bulk=BULK, platforms=PLATFORMS)


def comp(title, price, label=None):
    return Comp(title=title, price=price, sold_date=date(2026, 10, 1),
                url=f"https://ebay.com/itm/{abs(hash(title)) % 10**6}", condition_label=label)


class FakeSold:
    def __init__(self, comps=None, error=None):
        self.comps = comps or []
        self.error = error
        self.queries = []

    def fetch(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return list(self.comps)


class FakeEbay:
    def __init__(self, listings):
        self.listings = listings
        self.calls = []

    def search(self, query, fixed_price_only=False):
        self.calls.append((query, fixed_price_only))
        return list(self.listings)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.sqlite")
    yield c
    c.close()


def make_service(conn, sold, ebay=None, clock=None):
    return MarketService(conn, Config(), sold, ebay, CATALOG, clock or Clock())


# --- iqr_median / label_condition -------------------------------------------

def test_iqr_median_trims_outlier():
    assert iqr_median([100, 105, 110, 95, 900]) == (102.5, 4)


def test_iqr_median_small_sample():
    assert iqr_median([10, 1000]) == (505, 2)
    assert iqr_median([10, 20, 1000]) == (20, 3)


def test_iqr_median_empty():
    assert iqr_median([]) is None


@pytest.mark.parametrize("label, expected", [
    ("Brand New", "new"), ("New (Other)", "new"), ("Open Box", "new"), ("New", "new"),
    ("Pre-Owned", "used"), ("Used", "used"), ("Refurbished", "used"),
    ("Certified - Refurbished", "used"), ("Seller refurbished", "used"),
    ("Parts Only", None), ("For parts or not working", None), (None, None),
])
def test_label_condition(label, expected):
    assert label_condition(label) == expected


# --- product values ------------------------------------------------------------

def test_product_value_filters_variants_and_condition(conn):
    sold = FakeSold([
        comp("MSI RTX 4070 Ti Gaming X Trio", 600, "Pre-Owned"),
        comp("ASUS TUF RTX 4070 Ti OC", 620, "Used"),
        comp("Gigabyte 4070 Ti Eagle", 640),  # no label: title says used
        comp("RTX 4070 Ti Super Founders", 800, "Pre-Owned"),  # other variant
        comp("RTX 4070 Ti for parts", 200, "Pre-Owned"),  # title excluded
        comp("Zotac RTX 4070 Ti", 250, "For parts or not working"),  # parts label
        comp("PNY RTX 4070 Ti", 750, "Brand New"),  # new by label
        comp("RTX 4070 Ti factory sealed", 760),  # new by title
    ])
    mv = make_service(conn, sold).product_value(PRODUCTS[0], "used")
    assert sold.queries == ["rtx 4070 ti"]
    assert (mv.key, mv.condition, mv.method) == ("rtx-4070-ti", "used", "sold")
    assert mv.value == 620
    assert mv.comp_count == 3
    assert sorted(c.price for c in db.get_comps(conn, "rtx-4070-ti", "used")) == [600, 620, 640]


def test_product_value_new_condition(conn):
    sold = FakeSold([
        comp("PNY RTX 4070 Ti", 750, "Brand New"),
        comp("RTX 4070 Ti factory sealed", 770),
        comp("MSI RTX 4070 Ti", 600, "Pre-Owned"),
    ])
    mv = make_service(conn, sold).product_value(PRODUCTS[0], "new")
    assert (mv.value, mv.comp_count) == (760, 2)


def test_low_confidence_below_five(conn):
    four = [comp(f"RTX 4070 Ti card {i}", 600 + i, "Used") for i in range(4)]
    mv = make_service(conn, FakeSold(four)).product_value(PRODUCTS[0], "used")
    assert mv.comp_count == 4 and mv.low_confidence


def test_five_comps_full_confidence(conn):
    five = [comp(f"RTX 4070 Ti unit {i}", 600 + i, "Used") for i in range(5)]
    mv = make_service(conn, FakeSold(five)).product_value(PRODUCTS[0], "used")
    assert mv.comp_count == 5 and not mv.low_confidence


def test_cache_hit_within_ttl(conn):
    sold = FakeSold([comp("RTX 4070 Ti", 600, "Used")])
    clock = Clock()
    svc = make_service(conn, sold, clock=clock)
    first = svc.product_value(PRODUCTS[0], "used")
    clock.now = T0 + timedelta(hours=23)
    second = svc.product_value(PRODUCTS[0], "used")
    assert len(sold.queries) == 1
    assert second == first
    assert second.computed_at == T0


def test_cache_expired_refetches(conn):
    sold = FakeSold([comp("RTX 4070 Ti", 600, "Used")])
    clock = Clock()
    svc = make_service(conn, sold, clock=clock)
    svc.product_value(PRODUCTS[0], "used")
    sold.comps = [comp("RTX 4070 Ti", 700, "Used")]
    clock.now = T0 + timedelta(hours=25)
    svc.begin_run()
    mv = svc.product_value(PRODUCTS[0], "used")
    assert len(sold.queries) == 2
    assert mv.value == 700 and mv.computed_at == clock.now
    assert [c.price for c in db.get_comps(conn, "rtx-4070-ti", "used")] == [700]


def _listing(title, price, shipping, condition_id):
    return Listing(source="ebay", source_id=title, title=title, url=f"https://ebay.com/{price}",
                   price=price, shipping=shipping, shipping_offered=True, image_urls=[],
                   is_auction=False, end_time=None, condition_id=condition_id)


def test_fallback_to_active_bin(conn):
    ebay = FakeEbay([
        _listing("MSI RTX 4070 Ti", 640, 10.0, "3000"),
        _listing("ASUS RTX 4070 Ti", 660, None, "3000"),
        _listing("PNY RTX 4070 Ti", 800, 0.0, "1000"),  # new
        _listing("Zotac RTX 4070 Ti", 200, 0.0, "7000"),  # for parts
        _listing("RTX 4070 Ti Super", 850, 0.0, "3000"),  # other variant
    ])
    sold = FakeSold(error=SourceBlocked("blocked"))
    mv = make_service(conn, sold, ebay).product_value(PRODUCTS[0], "used")
    assert ebay.calls == [("rtx 4070 ti", True)]
    assert mv.method == "active_bin" and mv.low_confidence
    assert (mv.value, mv.comp_count) == (655, 2)
    assert db.get_market_value(conn, "rtx-4070-ti", "used") == mv


def test_fallback_when_no_sold_comps(conn):
    ebay = FakeEbay([_listing("MSI RTX 4070 Ti", 640, 0.0, "3000")])
    sold = FakeSold([comp("RTX 4070 Ti Super", 800, "Used")])
    mv = make_service(conn, sold, ebay).product_value(PRODUCTS[0], "used")
    assert mv.method == "active_bin" and mv.value == 640


def test_no_value_returns_none_and_caches_nothing(conn):
    svc = make_service(conn, FakeSold(error=SourceError("down")), None)
    assert svc.product_value(PRODUCTS[0], "used") is None
    svc = make_service(conn, FakeSold(), FakeEbay([]))
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert db.get_market_value(conn, "rtx-4070-ti", "used") is None


# --- bulk values ----------------------------------------------------------------

def test_bulk_value_for_gpu_uses_product_value(conn):
    sold = FakeSold([comp("RTX 4070 Ti", 600, "Used"), comp("RTX 4070 Ti", 900, "Brand New")])
    svc = make_service(conn, sold)
    mv = svc.bulk_value(BULK[0])
    assert (mv.key, mv.condition, mv.value) == ("rtx-4070-ti", "used", 600)
    assert svc.bulk_value(BULK[0]) == svc.product_value(PRODUCTS[0], "used")
    assert len(sold.queries) == 1


def test_bulk_value_regex_used_single_units(conn):
    sold = FakeSold([
        comp("Sony PS2 Slim Console SCPH-90001", 60, "Pre-Owned"),
        comp("PlayStation 2 console with 2 controllers", 80),
        comp("Lot of 3 PS2 consoles", 150, "Used"),  # a lot
        comp("PS2 fat console", 20, "For parts or not working"),  # parts label
        comp("PS2 slim console not working", 25, "Used"),  # not a working unit
        comp("PS2 console new in box", 300),  # new
        comp("PS2 controller", 15, "Used"),  # other bulk product
    ])
    mv = make_service(conn, sold).bulk_value(BULK[1])
    assert sold.queries == ["ps2 console"]
    assert (mv.key, mv.condition, mv.method) == ("ps2-console", "used", "sold")
    assert (mv.value, mv.comp_count) == (70, 2)


# --- per-game values --------------------------------------------------------------

def test_per_game_value(conn):
    sold = FakeSold([
        comp("Lot of 10 PS2 games", 50),
        comp("20 ps2 games", 80),
        comp("ps2 game lot", 30),
    ])
    mv = make_service(conn, sold).per_game_value(PLATFORMS[0])
    assert sold.queries == ["ps2 game lot"]
    assert (mv.key, mv.condition, mv.method) == ("ps2", "per_game", "sold")
    assert (mv.value, mv.comp_count) == (4.5, 2)
    assert mv.low_confidence
    assert len(db.get_comps(conn, "ps2", "per_game")) == 2


def test_per_game_value_has_no_active_bin_fallback(conn):
    ebay = FakeEbay([_listing("Lot of 10 PS2 games", 50, 0.0, "3000")])
    svc = make_service(conn, FakeSold(error=SourceError("down")), ebay)
    assert svc.per_game_value(PLATFORMS[0]) is None
    assert ebay.calls == []


# --- photo-item prices ---------------------------------------------------------------

def test_item_price_title_filter_and_30_day_ttl(conn):
    sold = FakeSold([
        comp("Chrono Trigger SNES cart", 300),
        comp("Chrono Trigger Super Nintendo SNES authentic", 320),
        comp("SNES Chrono Trigger cartridge tested", 310),
        comp("Chrono Trigger SNES repro", 40),
        comp("SNES game lot chrono trigger", 500),
        comp("Chrono Trigger SNES box only", 100),
        comp("Chrono Cross PS1", 90),  # missing a title word and the platform
    ])
    clock = Clock()
    svc = make_service(conn, sold, clock=clock)
    mv = svc.item_price("SNES", "Chrono Trigger", "loose")
    assert sold.queries == ["SNES Chrono Trigger"]
    assert (mv.key, mv.condition, mv.method) == ("snes|chrono trigger|game", "loose", "sold")
    assert (mv.value, mv.comp_count) == (310, 3)
    assert not mv.low_confidence  # min 3 comps for photo items
    assert db.get_market_value(conn, "snes|chrono trigger|game", "loose",
                               table="item_prices") == mv
    assert db.get_market_value(conn, "snes|chrono trigger", "loose") is None

    clock.now = T0 + timedelta(days=29)
    assert svc.item_price("snes", "chrono trigger", "unknown") == mv  # unknown priced as loose
    assert len(sold.queries) == 1

    clock.now = T0 + timedelta(days=31)
    svc.begin_run()
    svc.item_price("SNES", "Chrono Trigger", "loose")
    assert len(sold.queries) == 2


def test_item_price_query_by_completeness(conn):
    sold = FakeSold([comp("Chrono Trigger SNES complete in box", 600)])
    svc = make_service(conn, sold)
    mv = svc.item_price("SNES", "Chrono Trigger", "cib")
    svc.item_price("SNES", "Chrono Trigger", "sealed")
    assert sold.queries == ["SNES Chrono Trigger complete", "SNES Chrono Trigger sealed"]
    assert mv.condition == "cib" and mv.low_confidence


def test_item_price_ignores_short_and_stop_words(conn):
    sold = FakeSold([comp("Legend of Zelda Link to the Past SNES", 40)])
    mv = make_service(conn, sold).item_price("SNES", "The Legend of Zelda: A Link to the Past",
                                             "loose")
    assert mv.value == 40


# --- review fixes ----------------------------------------------------------------

def test_value_count_and_comps_agree_after_trimming(conn):
    raw = [76, 92, 100, 100, 107, 109, 138, 162, 199]
    sold = FakeSold([comp(f"RTX 4070 Ti unit {i}", v, "Used") for i, v in enumerate(raw)])
    mv = make_service(conn, sold).product_value(PRODUCTS[0], "used")
    assert iqr_median(raw) == (103.5, 8)
    assert mv.value == iqr_median(raw)[0]
    assert mv.comp_count == len(db.get_comps(conn, "rtx-4070-ti", "used")) == 8


class FailingEbay:
    def __init__(self):
        self.calls = 0

    def search(self, query, fixed_price_only=False):
        self.calls += 1
        raise SourceError("browse down")


def test_stale_product_value_returned_when_sold_blocked(conn):
    sold = FakeSold([comp("RTX 4070 Ti", 600, "Used")])
    clock = Clock()
    svc = make_service(conn, sold, None, clock)
    first = svc.product_value(PRODUCTS[0], "used")
    svc.begin_run()
    sold.error = SourceBlocked("blocked")
    clock.now = T0 + timedelta(hours=25)
    assert svc.product_value(PRODUCTS[0], "used") == first
    stored = db.get_market_value(conn, "rtx-4070-ti", "used")
    assert stored.computed_at == T0  # not re-saved, so the next run retries
    svc.begin_run()
    sold.error = None
    sold.comps = [comp("RTX 4070 Ti", 700, "Used")]
    assert svc.product_value(PRODUCTS[0], "used").value == 700


def test_stale_product_value_returned_when_browse_fallback_fails(conn):
    ebay = FailingEbay()
    sold = FakeSold([comp("RTX 4070 Ti", 600, "Used")])
    clock = Clock()
    svc = make_service(conn, sold, ebay, clock)
    first = svc.product_value(PRODUCTS[0], "used")
    svc.begin_run()
    sold.comps = []  # no sold comps, so the Browse fallback runs and fails
    clock.now = T0 + timedelta(hours=25)
    assert svc.product_value(PRODUCTS[0], "used") == first
    assert ebay.calls == 1
    assert db.get_market_value(conn, "rtx-4070-ti", "used").computed_at == T0


def test_stale_per_game_value_returned_when_fetch_fails(conn):
    sold = FakeSold([comp("Lot of 10 PS2 games", 50)])
    clock = Clock()
    svc = make_service(conn, sold, clock=clock)
    first = svc.per_game_value(PLATFORMS[0])
    svc.begin_run()
    sold.error = SourceError("down")
    clock.now = T0 + timedelta(hours=25)
    assert svc.per_game_value(PLATFORMS[0]) == first
    assert len(sold.queries) == 2


def test_active_bin_value_expires_after_two_hours(conn):
    ebay = FakeEbay([_listing("MSI RTX 4070 Ti", 640, 0.0, "3000")])
    sold = FakeSold(error=SourceError("down"))
    clock = Clock()
    svc = make_service(conn, sold, ebay, clock)
    assert svc.product_value(PRODUCTS[0], "used").method == "active_bin"
    svc.begin_run()
    clock.now = T0 + timedelta(hours=1, minutes=59)
    svc.product_value(PRODUCTS[0], "used")
    assert len(sold.queries) == 1  # still fresh
    clock.now = T0 + timedelta(hours=2)
    sold.error = None
    sold.comps = [comp("RTX 4070 Ti", 600, "Used")]
    mv = svc.product_value(PRODUCTS[0], "used")
    assert len(sold.queries) == 2
    assert (mv.method, mv.value) == ("sold", 600)


def test_bulk_value_when_source_product_disabled(conn):
    disabled = replace(PRODUCTS[0], enabled=False)
    catalog = Catalog(products=[disabled, PRODUCTS[1]], bulk=BULK, platforms=PLATFORMS)
    sold = FakeSold([comp("RTX 4070 Ti", 600, "Used"), comp("RTX 4070 Ti Super", 800, "Used")])
    svc = MarketService(conn, Config(), sold, None, catalog, Clock())
    mv = svc.bulk_value(BULK[0])
    assert (mv.key, mv.value, mv.comp_count) == ("rtx-4070-ti", 600, 1)


def test_sold_fetch_memoized_across_conditions(conn):
    ebay = FakeEbay([])
    sold = FakeSold([comp("RTX 4070 Ti", 600, "Used")])
    svc = make_service(conn, sold, ebay)
    assert svc.product_value(PRODUCTS[0], "used").value == 600
    assert svc.product_value(PRODUCTS[0], "new") is None  # no new comps; empty Browse too
    assert sold.queries == ["rtx 4070 ti"]
    assert ebay.calls == [("rtx 4070 ti", True)]


def test_miss_is_negative_cached_across_runs(conn):
    sold = FakeSold([])
    clock = Clock()
    svc = make_service(conn, sold, clock=clock)
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert len(sold.queries) == 1
    svc.begin_run()
    clock.now = T0 + timedelta(hours=23)
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert len(sold.queries) == 1  # memo cleared, negative cache still applies
    clock.now = T0 + timedelta(hours=24)
    sold.comps = [comp("RTX 4070 Ti", 600, "Used")]
    assert svc.product_value(PRODUCTS[0], "used").value == 600
    assert len(sold.queries) == 2


def test_item_price_negative_cache_uses_item_ttl(conn):
    sold = FakeSold([])
    clock = Clock()
    svc = make_service(conn, sold, clock=clock)
    assert svc.item_price("SNES", "Chrono Trigger", "loose") is None
    svc.begin_run()
    clock.now = T0 + timedelta(days=29)
    assert svc.item_price("SNES", "Chrono Trigger", "loose") is None
    assert len(sold.queries) == 1
    clock.now = T0 + timedelta(days=30)
    svc.item_price("SNES", "Chrono Trigger", "loose")
    assert len(sold.queries) == 2


def test_failed_fetch_without_cache_is_not_negative_cached(conn):
    sold = FakeSold(error=SourceError("down"))
    svc = make_service(conn, sold)
    assert svc.product_value(PRODUCTS[0], "used") is None
    svc.begin_run()
    sold.error = None
    sold.comps = [comp("RTX 4070 Ti", 600, "Used")]
    assert svc.product_value(PRODUCTS[0], "used").value == 600


def test_item_price_key_normalized(conn):
    sold = FakeSold([comp("Chrono Trigger SNES cart", 300)])
    svc = make_service(conn, sold)
    mv = svc.item_price("SNES", "Chrono Trigger\u00ae", "loose")
    assert mv.key == "snes|chrono trigger|game"
    assert svc.item_price("snes", "Chrono  Trigger", "loose") == mv
    assert len(sold.queries) == 1


# --- final review: games priced against same-name hardware ---------------------

HALO_COMPS = [
    comp("Xbox 360 Halo 3 Special Edition Console 20GB", 180),
    comp("Microsoft Xbox 360 Halo 3 Special Edition System", 185),
    comp("Halo 3 Xbox 360 Special Edition console bundle", 175),
    comp("Halo 3 Legendary Edition Spartan Helmet Xbox 360", 120),
    comp("Halo 4 Xbox 360 game", 15),  # another game: "3" is a title word
    comp("Halo 3 Xbox 360", 6),
    comp("Halo 3 Xbox 360 game disc tested", 7),
    comp("Xbox 360 Halo 3 loose disc", 5),
]


def test_game_item_not_priced_against_hardware(conn):
    sold = FakeSold(HALO_COMPS)
    mv = make_service(conn, sold).item_price("Xbox 360", "Halo 3", "loose", kind="game")
    assert mv.value == pytest.approx(6)
    assert mv.comp_count == 3
    assert [c.price for c in db.get_comps(conn, mv.key, "loose")] == [6, 7, 5]


def test_item_price_cache_key_includes_kind(conn):
    sold = FakeSold(HALO_COMPS)
    svc = make_service(conn, sold)
    game = svc.item_price("Xbox 360", "Halo 3", "loose", kind="game")
    console = svc.item_price("Xbox 360", "Halo 3", "loose", kind="console")
    assert (game.key, console.key) == ("xbox 360|halo 3|game", "xbox 360|halo 3|console")
    assert console.value > 100 and game.value < 10
    assert svc.item_price("Xbox 360", "Halo 3", "loose", kind="game") == game
    assert conn.execute("SELECT COUNT(*) FROM item_prices").fetchone()[0] == 2


@pytest.mark.parametrize("title, other", [
    ("Final Fantasy X", "Final Fantasy XII PS2"),
    ("Final Fantasy IV", "Final Fantasy VI PS2"),
    ("Grand Theft Auto V", "Grand Theft Auto IV PS2"),
])
def test_item_price_keeps_short_roman_and_digit_tokens(conn, title, other):
    sold = FakeSold([comp(f"{title} PS2", 20), comp(other, 90)])
    mv = make_service(conn, sold).item_price("PS2", title, "loose")
    assert (mv.value, mv.comp_count) == (20, 1)


def test_game_title_words_do_not_exclude_its_own_comps(conn):
    sold = FakeSold([comp("System Shock 2 PC big box", 60), comp("System Shock 2 PC bundle", 300)])
    mv = make_service(conn, sold).item_price("PC", "System Shock 2", "loose")
    assert (mv.value, mv.comp_count) == (60, 1)


# --- final review: any fetch failure is a SourceError ---------------------------


def test_unexpected_sold_fetch_error_is_memoized_source_error(conn):
    sold = FakeSold(error=ValueError("malformed page"))
    svc = make_service(conn, sold)
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert svc.item_price("SNES", "Chrono Trigger", "loose") is None
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert sold.queries == ["rtx 4070 ti", "SNES Chrono Trigger"]  # memoized per run


def test_unexpected_bin_fetch_error_is_source_error(conn):
    class BrokenEbay(FakeEbay):
        def search(self, query, fixed_price_only=False):
            self.calls.append((query, fixed_price_only))
            raise KeyError("itemSummaries")

    ebay = BrokenEbay([])
    svc = make_service(conn, FakeSold([]), ebay=ebay)
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert svc.product_value(PRODUCTS[0], "new") is None
    assert len(ebay.calls) == 1


def test_sold_block_stops_other_queries_for_the_run_and_recovers(conn):
    from dealfinder.market import SOLD_ERROR_SETTING

    sold = FakeSold(error=SourceBlocked("HTTP 403"))
    svc = make_service(conn, sold)
    assert svc.product_value(PRODUCTS[0], "used") is None
    assert svc.product_value(PRODUCTS[1], "used") is None
    assert svc.per_game_value(PLATFORMS[0]) is None
    assert sold.queries == ["rtx 4070 ti"]
    assert db.get_setting(conn, SOLD_ERROR_SETTING) == "HTTP 403"

    sold.error = None
    sold.comps = [comp("RTX 4070 Ti Used", 400)]
    svc.begin_run()
    assert svc.product_value(PRODUCTS[0], "used").value == 400
    assert db.get_setting(conn, SOLD_ERROR_SETTING) == ""
