from dealfinder.catalog import Catalog
from dealfinder.classify import classify
from dealfinder.config import Config
from dealfinder.models import BulkProduct, Classified, Listing, Platform, Product


def _plat(pid, match):
    return Platform(id=pid, name=pid, match=match, ebay_lot_query=f"{pid} game lot")


def _bulk(bid, category, match):
    return BulkProduct(id=bid, category=category, name=bid, ebay_query=bid, match=match,
                       repair_cost=25, success_rate=0.6)


def _prod(pid, category, match):
    return Product(id=pid, category=category, name=pid, ebay_query=pid, match=match)


CATALOG = Catalog(
    products=[
        _prod("4090", "gpu", r"\b4090\b"),
        _prod("ps5-spider-man-2", "console", r"\b(ps5|playstation 5)\b.*\bspider man 2\b"),
        _prod("ps2-slim", "console", r"\b(ps2|playstation 2)\b.*\b(console|slim)\b"),
        _prod("ps4-slim", "console", r"\b(ps4|playstation 4) slim\b"),
        _prod("switch-2", "console", r"\b(nintendo )?switch 2\b"),
    ],
    bulk=[
        _bulk("ps2-console", "console",
              r"\b(ps2|playstation 2)\b.*\b(consoles?|systems?|slim|fat|scph)\b"),
        _bulk("xbox-360-console", "console", r"\bxbox 360\b.*\b(consoles?|systems?)\b"),
        _bulk("xbox-360-controller", "controller", r"\bxbox 360\b.*\bcontrollers?\b"),
        _bulk("ps2-controller", "controller",
              r"\b(ps2|playstation 2)\b.*\b(controllers?|dualshock)\b"),
        _bulk("gameboy-console", "console", r"\bgame ?boy\b.*\b(consoles?|systems?)\b"),
        _bulk("gba-console", "console", r"\bgame ?boy advance( sp)?\b"),
        _bulk("ps5-console", "console", r"\b(ps5|playstation 5)\b.*\b(consoles?|systems?)\b"),
    ],
    platforms=[
        _plat("ps2", r"\b(ps2|playstation 2)\b"),
        _plat("ps3", r"\b(ps3|playstation 3)\b"),
        _plat("ps5", r"\b(ps5|playstation 5)\b"),
        _plat("xbox", r"\bxbox\b"),
        _plat("xbox-360", r"\bxbox 360\b"),
        _plat("ds", r"\b(nintendo )?ds\b"),
        _plat("3ds", r"\b(nintendo )?3ds\b"),
        _plat("ps4", r"\b(ps4|playstation 4)\b"),
        _plat("switch", r"\b(nintendo )?switch\b"),
        _plat("switch-2", r"\b(nintendo )?switch 2\b"),
        _plat("nes", r"\b(nes|nintendo entertainment system)\b"),
        _plat("snes", r"\b(snes|super nintendo( entertainment system)?)\b"),
        _plat("gameboy", r"\bgame ?boy\b"),
        _plat("gba", r"\bgame ?boy advance\b"),
    ],
)
CFG = Config()


def _listing(title, shipping_offered=True, condition_id=None):
    return Listing(source="ebay", source_id="1", title=title, url="u", price=10.0,
                   shipping=None, shipping_offered=shipping_offered, image_urls=[],
                   is_auction=False, end_time=None, condition_id=condition_id)


def _c(title, **kw):
    return classify(_listing(title, **kw), CATALOG, CFG)


def test_hardware_lot_for_parts_allowed():
    assert _c("Lot of 5 PS2 Slim consoles for parts") == Classified(
        kind="hardware_lot", key="ps2-console", condition=None, quantity=5, category="console")


def test_hardware_lot_below_min():
    assert _c("2x PS2 consoles") is None


def test_hardware_lot_shells_excluded():
    assert _c("lot of 6 ps2 shells only") is None


def test_mixed_hardware_lot_skipped():
    assert _c("lot 3 ps2 and 2 xbox 360 consoles") is None


def test_hardware_path_without_bulk_match_does_not_fall_back_to_single():
    assert _c("Lot of 3 RTX 4090") is None


def test_game_lot():
    assert _c("Lot of 20 PS2 games") == Classified(
        kind="game_lot", key="ps2", condition=None, quantity=20, category=None)


def test_game_lot_untested_excluded():
    assert _c("Lot of 20 PS2 games untested") is None


def test_game_lot_repro_excluded():
    assert _c("Lot of 10 DS games repro") is None


def test_game_lot_below_min():
    assert _c("3 ds games") is None


def test_platform_specificity():
    assert _c("15 Xbox 360 games").key == "xbox-360"
    assert _c("12 3DS games").key == "3ds"


def test_ps2_game_lot_not_a_console():
    r = _c("PS2 game lot 10 games")
    assert (r.kind, r.key, r.quantity) == ("game_lot", "ps2", 10)


def test_console_with_games_skipped():
    assert _c("PS2 console with 10 games") is None
    assert _c("PS2 Slim console + 12 games") is None
    assert _c("PS2 1 console 12 games") is None
    assert _c("Lot of 2 PS2 consoles and games") is None


def test_console_bundle_with_game_is_single():
    r = _c("PS5 Spider-Man 2 Limited Edition Console Bundle with Game")
    assert (r.kind, r.key, r.quantity, r.category) == ("single", "ps5-spider-man-2", 1, "console")


def test_single_with_game_and_no_hardware_word_reaches_single_path():
    r = _c("PS5 Spider-Man 2 Limited Edition Bundle with game")
    assert (r.kind, r.key) == ("single", "ps5-spider-man-2")
    assert _c("PS2 game Ratchet and Clank") is None


def test_unknown_count_is_scan_only():
    assert _c("Box of PS2 games") == Classified(
        kind="scan_only", key="ps2", condition=None, quantity=None, category=None)


def test_unknown_count_hardware_is_scan_only():
    r = _c("Box of PS2 consoles")
    assert (r.kind, r.key, r.quantity, r.category) == ("scan_only", "ps2-console", None, "console")


def test_pickup_only_dropped():
    assert _c("Lot of 20 PS2 games", shipping_offered=False) is None
    assert _c("RTX 4090 Founders Edition", shipping_offered=False) is None


def test_single_path():
    assert _c("RTX 4090 Founders Edition") == Classified(
        kind="single", key="4090", condition="used", quantity=1, category="gpu")
    assert _c("RTX 4090 Founders Edition", condition_id="1000").condition == "new"


def test_single_path_rejects_unmatched():
    assert _c("Random gaming mouse") is None


def _kkq(title):
    r = _c(title)
    return None if r is None else (r.kind, r.key, r.quantity)


def test_accessory_clause_does_not_pick_product():
    assert _kkq("Lot of 4 Xbox 360 consoles with controllers") == (
        "hardware_lot", "xbox-360-console", 4)
    assert _kkq("Lot of 3 PS2 Slim consoles with 6 controllers") == (
        "hardware_lot", "ps2-console", 3)
    assert _kkq("Lot of 10 Xbox 360 controllers and 1 console") == (
        "hardware_lot", "xbox-360-controller", 10)


def test_single_console_with_one_game_reaches_single_path():
    assert _kkq("PS4 Slim 500 GB console with game") == ("single", "ps4-slim", 1)
    assert _kkq("Nintendo Switch 2 console with game") == ("single", "switch-2", 1)
    assert _kkq("PS5 Console Spider-Man 2 Bundle with Game") == ("single", "ps5-spider-man-2", 1)


def test_platform_names_are_not_game_or_hardware_words():
    assert _kkq("Lot of 15 NES Nintendo Entertainment System games") == ("game_lot", "nes", 15)
    assert _kkq("Lot of 12 Super Nintendo Entertainment System games") == (
        "game_lot", "snes", 12)
    assert _kkq("Lot of 5 Game Boy consoles for parts") == ("hardware_lot", "gameboy-console", 5)
    assert _kkq("Lot of 6 Game Boy Advance SP") == ("hardware_lot", "gba-console", 6)


def test_disc_edition_is_not_a_game_word():
    assert _kkq("Lot of 3 PS5 Disc Edition consoles for parts") == (
        "hardware_lot", "ps5-console", 3)


def test_slim_is_a_hardware_word():
    assert _c("PS2 Slim lot of 5 with 10 games") is None
    assert _c("lot of 5 ps2 slim games only") is None
