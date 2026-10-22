import re
from pathlib import Path

import pytest

from dealfinder import db
from dealfinder.catalog import load_catalog, load_seed
from dealfinder.classify import classify
from dealfinder.config import Config
from dealfinder.matcher import match_single
from dealfinder.models import Listing
from dealfinder.text import normalize

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
SEED = load_seed(CATALOG_DIR)


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    conn = db.connect(tmp_path_factory.mktemp("seed") / "t.db")
    yield load_catalog(CATALOG_DIR, conn, Config())
    conn.close()


def _classify(title, catalog):
    listing = Listing(source="ebay", source_id="1", title=title, url="u", price=10.0,
                      shipping=None, shipping_offered=True, image_urls=[], is_auction=False,
                      end_time=None)
    return classify(listing, catalog, Config())


def test_seed_loads():
    assert sum(p.category == "gpu" for p in SEED.products) >= 25
    assert sum(p.category == "cpu" for p in SEED.products) >= 50
    assert len(SEED.platforms) >= 25


@pytest.mark.parametrize("title, expected", [
    ("AMD Ryzen 9 7900X", "ryzen-7900x"),
    ("Ryzen 7 7800X3D", "ryzen-7800x3d"),
    ("Ryzen 7 7700X", "ryzen-7700x"),
    ("i7-13700KF", "i7-13700kf"),
    ("Intel Core i7 13700K", "i7-13700k"),
    ("RTX 3060 12GB", "rtx-3060-12gb"),
    ("RTX 3060 Ti", "rtx-3060-ti"),
    ("RTX 5060 Ti 16GB", "rtx-5060-ti-16gb"),
    ("RTX 4080 Super", "rtx-4080-super"),
    # beyond the brief's list: neighbours that must stay apart
    ("AMD Ryzen 5 7600", "ryzen-7600"),
    ("RTX 4060 Ti 16GB", "rtx-4060-ti-16gb"),
    ("RTX 3080 Ti 12GB", "rtx-3080-ti"),
    ("RTX 3080 12GB LHR", "rtx-3080-12gb"),
    ("RTX4070TiSuper 16GB", "rtx-4070-ti-super"),
    ("Intel Core Ultra 7 265KF", "core-ultra-7-265kf"),
    ("i9-14900KS", "i9-14900ks"),
    ("Sony PS5 Pro 30th Anniversary Limited Edition Bundle", "ps5-pro-30th-anniversary"),
    ("PS5 Digital Edition 30th Anniversary Limited Edition Bundle", "ps5-30th-anniversary"),
    ("PlayStation Portal Remote Player PS5 30th Anniversary", "playstation-portal-30th-anniversary"),
    ("PS5 DualSense 30th Anniversary Wireless Controller", "dualsense-30th-anniversary"),
    ("DualSense Edge 30th Anniversary Limited Edition", "dualsense-edge-30th-anniversary"),
    ("PS5 30th Anniversary DualSense Edge Controller", "dualsense-edge-30th-anniversary"),
    ("PS5 30th Anniversary Console Bundle with DualSense", "ps5-30th-anniversary"),
    ("Nintendo Switch OLED Model Zelda Tears of the Kingdom Edition",
     "switch-oled-zelda-totk"),
    ("Nintendo Switch Pro Controller Zelda Tears of the Kingdom Edition",
     "switch-pro-controller-zelda-totk"),
    # review round 1: spaced CPU suffixes
    ("AMD Ryzen 9 7900 X3D", "ryzen-7900x3d"),
    ("AMD Ryzen 7 7700 X", "ryzen-7700x"),
    ("Intel Core i7 13700 K", "i7-13700k"),
    ("Intel Core i5 12400 F", "i5-12400f"),
    ("Intel Core i7 13700 KF", "i7-13700kf"),
    # reversed word order on limited editions
    ("God of War Ragnarok DualSense Controller Limited Edition", "dualsense-god-of-war-ragnarok"),
    ("Marvel's Spider-Man 2 Limited Edition DualSense Controller", "dualsense-spider-man-2"),
    ("PS5 Astro Bot Limited Edition DualSense", "dualsense-astro-bot"),
    ("Zelda Tears of the Kingdom Nintendo Switch OLED", "switch-oled-zelda-totk"),
    ("Marvel's Spider-Man 2 PS5 Limited Edition Console Bundle", "ps5-spider-man-2"),
    ("Nintendo Switch OLED Zelda TOTK Edition Console with Dock", "switch-oled-zelda-totk"),
    ("PS5 Spider-Man 2 Limited Edition Console Bundle with Game", "ps5-spider-man-2"),
    # "12G" VRAM spellings, size-less 3080
    ("Gigabyte RTX 3060 Gaming OC 12G", "rtx-3060-12gb"),
    ("Zotac RTX 3080 Trinity 10G", "rtx-3080-10gb"),
    ("RTX 4060 Ti 16G", "rtx-4060-ti-16gb"),
    ("NVIDIA GeForce RTX 3080 Founders Edition", "rtx-3080-10gb"),
    ("EVGA RTX 3080 FTW3 Ultra 12GB", "rtx-3080-12gb"),
    ("AMD Ryzen 9 7900X", "ryzen-7900x"),
])
def test_single_collisions(title, expected):
    result = match_single(title, SEED.products)
    assert result is not None and result[0].id == expected


@pytest.mark.parametrize("title", [
    "RTX 3060",            # no VRAM size: neither 3060 variant
    "RTX 4060 Ti",         # no VRAM size: neither Ti variant, and not the plain 4060
    "Ryzen 7 5800XT",      # not in the catalog; "xt" must not turn it into a 5800X3D
    # GPUs are NVIDIA RTX only; a Radeon title must never become a Ryzen CPU
    "Sapphire RX 7900 XT 20GB",
    "RX 7900XTX Nitro",
    "AMD 7900XTX 24GB Reference",
    "AMD 7900 XT",
    "XFX Radeon RX 7600 8GB",
    "PowerColor Radeon 7700 XT",
    "Intel Arc B580 12GB",
    "PS5 Astro Bot Joyful DualSense",
    "RTX 3050 6GB Low Profile",
    "God of War Ragnarök PS5 Bundle with DualSense",
    # accessories named after limited-edition consoles
    "PlayStation 5 Console Covers 30th Anniversary Limited Edition",
    "PS5 Pro 30th Anniversary Console Covers",
    "PS5 Spider-Man 2 Limited Edition Console Cover",
    "PS5 30th Anniversary vertical stand",
    "Xbox Series X Halo Infinite console wrap",
    "Nintendo Switch OLED Zelda Tears of the Kingdom carrying case",
    "Switch OLED Zelda TOTK Edition dock only",
    "Spider-Man 2 PS5 game Limited Edition steelbook",
    "Pokemon Scarlet Nintendo Switch OLED game",
])
def test_single_no_match(title):
    assert match_single(title, SEED.products) is None


@pytest.mark.parametrize("title, kind, key", [
    ("Lot of 4 Nintendo Switch Lite consoles", "hardware_lot", "switch-lite-console"),
    ("3 Nintendo Switch 2 consoles", "hardware_lot", "switch-2-console"),
    ("lot 5 Nintendo 3DS XL systems", "hardware_lot", "3ds-console"),
    ("10 Nintendo DS games", "game_lot", "ds"),
    ("8 Xbox One games", "game_lot", "xbox-one"),
    ("6 original Xbox games", "game_lot", "xbox"),
    ("Lot of 15 NES Nintendo Entertainment System games", "game_lot", "nes"),
    ("Lot of 6 Game Boy Advance SP", "hardware_lot", "gba-console"),
    # beyond the brief's list
    ("Lot of 5 Xbox 360 consoles", "hardware_lot", "xbox-360-console"),
    ("Lot of 4 original Xbox consoles", "hardware_lot", "xbox-console"),
    ("Lot of 3 Xbox Series X consoles", "hardware_lot", "xbox-series-x-console"),
    ("Lot of 3 Nintendo Switch consoles with Joy-Cons", "hardware_lot", "switch-console"),
    ("Lot of 3 Nintendo Switch consoles with 2 Joy-Cons", "hardware_lot", "switch-console"),
    ("Lot of 4 New Nintendo 3DS XL", "hardware_lot", "new-3ds-console"),
    ("Lot of 5 Nintendo DS Lite", "hardware_lot", "ds-lite-console"),
    ("Lot of 5 PlayStation 2 Slim consoles", "hardware_lot", "ps2-console"),
    ("Lot of 4 PS1 consoles", "hardware_lot", "ps1-console"),
    ("Lot of 6 PS4 DualShock 4 controllers", "hardware_lot", "dualshock-4"),
    ("Lot of 5 Wii U consoles", "hardware_lot", "wii-u-console"),
    ("20 Super Nintendo SNES games", "game_lot", "snes"),
    ("Lot of 12 Game Boy Color games", "game_lot", "gameboy-color"),
    ("Lot of 10 Sega Genesis games", "game_lot", "genesis"),
    ("Lot of 9 Nintendo 64 games", "game_lot", "n64"),
    ("Lot of 7 PS Vita games", "game_lot", "ps-vita"),
    ("Lot of 5 3DS XL systems with chargers", "hardware_lot", "3ds-console"),
    ("Lot of 4 Nintendo DS Lite with chargers", "hardware_lot", "ds-lite-console"),
    ("Lot of 5 Nintendo DS Lite systems", "hardware_lot", "ds-lite-console"),
    ("Lot of 3 Game Boy Color systems", "hardware_lot", "gameboy-color-console"),
    ("Lot of 3 PS5 Slim Disc consoles", "hardware_lot", "ps5-console"),
    ("Lot of 10 PS2 games with cases", "game_lot", "ps2"),
    ("Lot of 5 PS4 controllers", "hardware_lot", "dualshock-4"),
    ("Lot of 6 DualSense controllers", "hardware_lot", "dualsense"),
])
def test_lot_collisions(catalog, title, kind, key):
    result = _classify(title, catalog)
    assert result is not None and (result.kind, result.key) == (kind, key)


@pytest.mark.parametrize("title, key", [
    # fan-count names ("Ventus 3X") are not quantities
    ("MSI GeForce RTX 4070 Ventus 3X 12G OC", "rtx-4070"),
    ("Gigabyte RTX 3080 Windforce 3X 10GB", "rtx-3080-10gb"),
    ("MSI RTX 3060 Ventus 2X 12G OC", "rtx-3060-12gb"),
])
def test_classify_single(catalog, title, key):
    result = _classify(title, catalog)
    assert result is not None and (result.kind, result.key, result.quantity) == ("single", key, 1)


@pytest.mark.parametrize("title", [
    # handheld accessory lots
    "Lot of 10 Nintendo DS styluses",
    "Lot of 20 Nintendo 3DS screen protectors",
    "Lot of 10 Game Boy Advance SP chargers",
    "Lot of 4 GBA link cables",
    "Lot of 5 PSP batteries",
    "Lot of 5 PSP UMD cases",
    "Lot of 5 PS Vita chargers",
    "Lot of 4 Nintendo Switch Lite cases",
    # controller accessory lots
    "Lot of 5 PS4 controller chargers",
    "Lot of 4 PS5 controller charging stations",
    "Lot of 5 Xbox 360 controller battery packs",
    "Lot of 4 DualSense charging stations",
    "Lot of 6 Xbox One controller skins",
    # Switch 2 pads are not Switch 1 pads
    "Lot of 3 Nintendo Switch 2 Pro Controllers",
    "Lot of 4 Switch 2 Joy-Con",
    # game cases are not games
    "Lot of 5 Nintendo DS game cases",
])
def test_lot_rejected(catalog, title):
    assert _classify(title, catalog) is None


def test_every_pattern_matches_its_own_name():
    for entry in (*SEED.products, *SEED.bulk, *SEED.platforms):
        assert re.search(entry.match, normalize(entry.name)), entry.id
