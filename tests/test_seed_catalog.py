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
    assert sum(p.category == "gpu" for p in SEED.products) >= 50
    assert sum(p.category == "cpu" for p in SEED.products) >= 50
    assert len(SEED.platforms) >= 25


@pytest.mark.parametrize("title, expected", [
    ("AMD Ryzen 9 7900X", "ryzen-7900x"),
    ("Sapphire RX 7900 XT 20GB", "rx-7900-xt"),
    ("RX 7900XTX Nitro", "rx-7900-xtx"),
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
    ("XFX Radeon RX 7600 8GB", "rx-7600"),
    ("ASRock RX 7700 XT", "rx-7700-xt"),
    ("RX 7900 GRE 16GB", "rx-7900-gre"),
    ("RX9070XT 16GB", "rx-9070-xt"),
    ("RX 9060 XT 8GB", "rx-9060-xt-8gb"),
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
])
def test_single_collisions(title, expected):
    result = match_single(title, SEED.products)
    assert result is not None and result[0].id == expected


@pytest.mark.parametrize("title", [
    "RTX 3060",            # no VRAM size: neither 3060 variant
    "RTX 4060 Ti",         # no VRAM size: neither Ti variant, and not the plain 4060
    "AMD 7900 XT",         # not a CPU, and AMD GPUs need rx / radeon
    "Ryzen 7 5800XT",      # not in the catalog; "xt" must not turn it into a 5800X3D
    "PS5 Astro Bot Joyful DualSense",
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
])
def test_lot_collisions(catalog, title, kind, key):
    result = _classify(title, catalog)
    assert result is not None and (result.kind, result.key) == (kind, key)


def test_every_pattern_matches_its_own_name():
    for entry in (*SEED.products, *SEED.bulk, *SEED.platforms):
        assert re.search(entry.match, normalize(entry.name)), entry.id
