from dealfinder.lots import match_game_lot, match_hardware_lot
from dealfinder.models import BulkProduct, Platform
from dealfinder.text import normalize, parse_quantity


def _plat(pid, match, **kw):
    return Platform(id=pid, name=pid, match=match, ebay_lot_query=f"{pid} game lot", **kw)


def _bulk(bid, category, match, **kw):
    return BulkProduct(id=bid, category=category, name=bid, ebay_query=bid, match=match,
                       repair_cost=25, success_rate=0.6, **kw)


PLATFORMS = [
    _plat("ps2", r"\b(ps2|playstation 2)\b"),
    _plat("ps3", r"\b(ps3|playstation 3)\b"),
    _plat("xbox", r"\bxbox\b"),
    _plat("xbox-360", r"\bxbox 360\b"),
    _plat("ds", r"\b(nintendo )?ds\b"),
    _plat("3ds", r"\b(nintendo )?3ds\b"),
]
MASKS = [p.match for p in PLATFORMS]
BULK = [
    _bulk("ps2-console", "console", r"\b(ps2|playstation 2)\b.*\b(consoles?|systems?|slim|fat|scph)\b"),
    _bulk("xbox-360-console", "console", r"\bxbox 360\b.*\b(consoles?|systems?)\b"),
    _bulk("xbox-360-controller", "controller", r"\bxbox 360\b.*\bcontrollers?\b"),
]


def _hw(title, bulk=BULK, min_qty=3, platforms=PLATFORMS):
    t = normalize(title)
    r = match_hardware_lot(t, bulk, parse_quantity(t, MASKS), min_qty, platforms=platforms)
    return None if r is None else (r[0].id, r[1])


def _games(title, platforms=PLATFORMS, min_qty=5):
    t = normalize(title)
    r = match_game_lot(t, platforms, parse_quantity(t, MASKS), min_qty)
    return None if r is None else (r[0].id, r[1])


def test_hardware_lot_for_parts_allowed():
    assert _hw("Lot of 5 PS2 Slim consoles for parts") == ("ps2-console", 5)


def test_hardware_lot_broken_untested_as_is_allowed():
    assert _hw("Lot of 4 Xbox 360 controllers broken untested as-is") == ("xbox-360-controller", 4)


def test_hardware_lot_below_min():
    assert _hw("2x PS2 consoles") is None


def test_hardware_lot_unknown_count_kept():
    assert _hw("Box of PS2 consoles") == ("ps2-console", None)


def test_hardware_lot_shells_excluded():
    assert _hw("lot of 6 ps2 shells only") is None
    assert _hw("lot of 6 ps2 slim consoles shells only") is None


def test_hardware_lot_other_excludes():
    for title in ("Lot of 5 PS2 console boxes only", "lot of 5 ps2 systems empty box",
                  "lot of 5 ps2 console case only", "lot of 5 ps2 slim games only"):
        assert _hw(title) is None, title


def test_mixed_hardware_lot_skipped():
    assert _hw("lot 3 ps2 and 2 xbox 360 consoles") is None


def test_mixed_bulk_products_skipped():
    bulk = [_bulk("bulk-3080", "gpu", r"\b3080\b"), _bulk("bulk-3090", "gpu", r"\b3090\b")]
    assert _hw("Lot of 4 RTX 3080 and 3090 for parts", bulk=bulk) is None


def test_nested_platforms_not_mixed():
    assert _hw("Lot of 5 Xbox 360 controllers") == ("xbox-360-controller", 5)


def test_hardware_lot_no_bulk_match():
    assert _hw("Lot of 5 Wii consoles") is None


def test_hardware_lot_disabled_winner_not_downgraded():
    bulk = [_bulk("ps2-console", "console", r"\bps2\b.*\bconsoles?\b"),
            _bulk("ps2-slim-console", "console", r"\bps2 slim consoles?\b", enabled=False)]
    assert _hw("Lot of 5 PS2 slim consoles", bulk=bulk) is None


def test_game_lot():
    assert _games("Lot of 20 PS2 games") == ("ps2", 20)


def test_game_lot_untested_excluded():
    assert _games("Lot of 20 PS2 games untested") is None


def test_game_lot_repro_excluded():
    assert _games("Lot of 10 GBA repro carts DS") is None
    assert _games("10 PS2 games reproduction") is None


def test_game_lot_other_excludes():
    for title in ("20 PS2 games cases only", "Lot of 10 ps2 games scratched",
                  "15 ps3 games as is", "10 ds games manuals only", "10 ps2 games empty case"):
        assert _games(title) is None, title


def test_game_lot_below_min():
    assert _games("3 ds games") is None


def test_game_lot_unknown_count_kept():
    assert _games("Box of PS2 games") == ("ps2", None)


def test_platform_specificity():
    assert _games("15 Xbox 360 games") == ("xbox-360", 15)
    assert _games("12 3DS games") == ("3ds", 12)
    assert _games("Lot of 8 original Xbox games") == ("xbox", 8)


def test_mixed_game_lot_skipped():
    assert _games("Lot of 20 PS2 and PS3 games") is None


def test_game_lot_disabled_winner_not_downgraded():
    platforms = [_plat("xbox", r"\bxbox\b"), _plat("xbox-360", r"\bxbox 360\b", enabled=False)]
    assert _games("15 Xbox 360 games", platforms=platforms) is None
