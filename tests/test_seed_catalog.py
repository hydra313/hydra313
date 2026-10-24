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
    ("AMD Ryzen 5 7600 6-Core Processor", "ryzen-7600"),
    ("Ryzen 7 7800X3D AM5 CPU", "ryzen-7800x3d"),
])
def test_single_collisions(title, expected):
    result = match_single(title, SEED.products)
    assert result is not None and result[0].id == expected


@pytest.mark.parametrize("title", [
    "RTX 3060",            # no VRAM size: neither 3060 variant
    "RTX 4060 Ti",         # no VRAM size: neither Ti variant, and not the plain 4060
    "Ryzen 7 5800XT",      # not in the catalog; "xt" must not turn it into a 5800X3D
    "Intel Core i7-7700 SR338 3.60GHz Desktop Processor",  # live ShopGoodwill title
    "Intel Core i9-7900X CPU",
    "Intel Core i9-9900X Processor",
    "Intel 7700 processor",
    # GPUs are NVIDIA RTX only; a Radeon title must never become a Ryzen CPU
    "Sapphire RX 7900 XT 20GB",
    "RX 7900XTX Nitro",
    "AMD 7900XTX 24GB Reference",
    "AMD 7900 XT",
    "XFX Radeon RX 7600 8GB",
    "PowerColor Radeon 7700 XT",
    "Intel Arc B580 12GB",
    # Radeon cards named without rx / radeon are not Ryzen CPUs
    "Sapphire Pulse 7600 8GB",
    "XFX Speedster SWFT 7600 8GB",
    "ASRock Challenger 7600 8GB GDDR6",
    "Gigabyte 7600 Gaming OC 8G",
    "AMD 7900 20GB",
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


@pytest.mark.parametrize("title", ["Lot of 5 Intel Core i7-7700 CPUs for parts",
                                   "Lot of 3 Intel Core i9-7900X CPUs untested"])
def test_intel_cpu_lots_do_not_become_ryzen(catalog, title):
    assert _classify(title, catalog) is None


@pytest.mark.parametrize("title, key", [("AMD Ryzen 7 7700 CPU", "ryzen-7700"),
                                         ("7700 CPU", "ryzen-7700"),
                                         ("AMD Ryzen 9 7900X 12 Core CPU", "ryzen-7900x")])
def test_ryzen_brand_check_preserves_valid_titles(title, key):
    result = match_single(title, SEED.products)
    assert result is not None and result[0].id == key


@pytest.mark.parametrize("title, key, quantity", [
    ("Lot of 5 Original Microsoft Xbox Controller S Wired for Xbox Console Black",
     "xbox-controller", 5),
    ("3pc Sony PlayStation Console Lot for Parts or Repair", "ps1-console", 3),
])
def test_live_hardware_titles(catalog, title, key, quantity):
    result = _classify(title, catalog)
    assert result is not None
    assert (result.kind, result.key, result.quantity) == ("hardware_lot", key, quantity)


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
    # accessories listed as extras of a controller lot
    ("Lot of 3 PS4 controllers with charging dock", "hardware_lot", "dualshock-4"),
    ("Lot of 3 Joy-Con with straps", "hardware_lot", "joy-con"),
    ("Lot of 3 Xbox 360 wireless controllers with battery packs", "hardware_lot",
     "xbox-360-controller"),
    ("Lot of 6 Wii Remotes with jackets", "hardware_lot", "wii-remote"),
    ("Lot of 4 PS4 controllers w/ chargers", "hardware_lot", "dualshock-4"),
    ("Lot of 4 Xbox One controllers and charging station", "hardware_lot",
     "xbox-wireless-controller"),
    ("Lot of 4 Joy-Cons with grips", "hardware_lot", "joy-con"),
    ("Lot of 5 Ryzen 5 7600 CPUs", "hardware_lot", "bulk-ryzen-7600"),
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
    # Radeon cards named without rx / radeon are not Ryzen lots either
    "Lot of 3 Sapphire Pulse 7600 8GB",
    "Lot of 4 AMD 7900 20GB",
    # game cases are not games
    "Lot of 5 Nintendo DS game cases",
])
def test_lot_rejected(catalog, title):
    assert _classify(title, catalog) is None


def test_every_pattern_matches_its_own_name():
    for entry in (*SEED.products, *SEED.bulk, *SEED.platforms):
        assert re.search(entry.match, normalize(entry.name)), entry.id


# Collectible limited-edition consoles of any generation (spec 5.1)
@pytest.mark.parametrize("title, key", [
    ("Xbox 360 Halo 3 Special Edition Console", "xbox-360-halo-3-special-edition"),
    ("Halo 3 Limited Edition Xbox 360 Console 20GB", "xbox-360-halo-3-special-edition"),
    ("Microsoft Xbox 360 Halo 3 Spartan Edition", "xbox-360-halo-3-special-edition"),
    ("Xbox 360 Halo 3 Special Edition 20GB Green & Gold Console w/ Controller",
     "xbox-360-halo-3-special-edition"),
    ("Microsoft Xbox 360 S Halo: Reach Limited Edition 250GB Console",
     "xbox-360-halo-reach-limited-edition"),
    ("Halo Reach Limited Edition Xbox 360 Slim Console Bundle",
     "xbox-360-halo-reach-limited-edition"),
    ("Xbox 360 Star Wars R2-D2 Limited Edition Console 320GB", "xbox-360-star-wars-r2-d2"),
    ("Xbox 360 Kinect Star Wars Limited Edition Bundle R2D2", "xbox-360-star-wars-r2-d2"),
    ("Original Xbox Halo Special Edition Green Console", "xbox-halo-special-edition"),
    ("Microsoft Xbox Translucent Green Halo Edition Console with Controller",
     "xbox-halo-special-edition"),
    ("Xbox One X Cyberpunk 2077 Limited Edition Console Bundle 1TB",
     "xbox-one-x-cyberpunk-2077"),
    ("Microsoft Xbox One X 1TB Cyberpunk 2077 Limited Edition Console",
     "xbox-one-x-cyberpunk-2077"),
    ("Sony PlayStation 4 20th Anniversary Edition 500GB Console", "ps4-20th-anniversary"),
    ("20th Anniversary PS4 Console Gray", "ps4-20th-anniversary"),
    ("PS4 Pro 500 Million Limited Edition 2TB Console", "ps4-pro-500-million"),
    ("Sony PlayStation 4 Pro 2TB 500 Million Limited Edition", "ps4-pro-500-million"),
    ("Nintendo 64 Pikachu Edition Console", "n64-pikachu"),
    ("Pikachu N64 Console Pokemon Edition with Controller", "n64-pikachu"),
    ("Panasonic Q GameCube Console SL-GC10", "gamecube-panasonic-q"),
    ("Panasonic Q Nintendo GameCube DVD Player System", "gamecube-panasonic-q"),
    ("Game Boy Advance SP Groudon Edition", "gba-sp-groudon"),
    ("Pokemon Center Groudon GBA SP AGS-001", "gba-sp-groudon"),
    ("Game Boy Advance SP Kyogre Edition", "gba-sp-kyogre"),
    ("Nintendo GBA SP Kyogre Pokemon Center Limited", "gba-sp-kyogre"),
    ("Game Boy Advance SP Pikachu Edition", "gba-sp-pikachu"),
    ("Pokemon Pikachu GBA SP with Charger", "gba-sp-pikachu"),
    ("Nintendo DS Lite Dialga Palkia Pokemon Center Edition", "ds-lite-dialga-palkia"),
    ("DS Lite Pokemon Dialga & Palkia Limited Edition", "ds-lite-dialga-palkia"),
    ("Nintendo DS Lite Pikachu Edition Yellow", "ds-lite-pikachu"),
    ("Pokemon Center Pikachu DS Lite with Stylus", "ds-lite-pikachu"),
    ("New Nintendo 3DS XL Majora's Mask Limited Edition", "new-3ds-xl-majoras-mask"),
    ("Zelda Majoras Mask New 3DS XL Console", "new-3ds-xl-majoras-mask"),
    ("Nintendo 3DS Zelda 25th Anniversary Edition", "3ds-zelda-25th-anniversary"),
    ("Legend of Zelda 25th Anniversary 3DS Console Black", "3ds-zelda-25th-anniversary"),
    ("New Nintendo 3DS XL Pikachu Yellow Edition", "new-3ds-xl-pikachu-yellow"),
    ("Pikachu New 3DS XL Pokemon Edition", "new-3ds-xl-pikachu-yellow"),
    ("Wii U Deluxe Zelda Wind Waker HD Edition 32GB Console", "wii-u-zelda-wind-waker"),
    ("Nintendo Wii U Wind Waker Limited Edition Deluxe Set", "wii-u-zelda-wind-waker"),
    ("Sega Dreamcast Hello Kitty Edition Console", "dreamcast-hello-kitty"),
    ("Hello Kitty Dreamcast System Pink", "dreamcast-hello-kitty"),
    # fix round 1: a bundled game, charger or controller after with / and / +
    ("Xbox 360 Halo 3 Special Edition Console with Halo 3 game, controller and cables",
     "xbox-360-halo-3-special-edition"),
    ("Xbox 360 Halo 3 Special Edition Console w/ Halo 3 game & controller",
     "xbox-360-halo-3-special-edition"),
    ("Microsoft Xbox 360 Halo 3 Special Edition 20GB Console + Halo 3 Game",
     "xbox-360-halo-3-special-edition"),
    ("Xbox 360 Halo 3 Special Edition Console & Halo 3 Game, Matching Controller",
     "xbox-360-halo-3-special-edition"),
    ("Xbox 360 Halo 3 Special Edition Console W/ Halo 3 Game", "xbox-360-halo-3-special-edition"),
    ("Xbox 360 Halo 3 Special Edition Console with charger", "xbox-360-halo-3-special-edition"),
    ("Xbox 360 S Halo Reach Limited Edition Console 250GB with Halo Reach Game",
     "xbox-360-halo-reach-limited-edition"),
    ("Original Xbox Halo Special Edition Green Console with Halo game",
     "xbox-halo-special-edition"),
    ("Original Xbox Halo Special Edition Console + Halo Combat Evolved game",
     "xbox-halo-special-edition"),
    ("Game Boy Advance SP Pikachu with Pokemon Ruby game", "gba-sp-pikachu"),
    ("Nintendo DS Lite Pikachu Edition with Pokemon Diamond Game", "ds-lite-pikachu"),
    ("Nintendo 64 Pikachu Edition Console with Hey You Pikachu game", "n64-pikachu"),
    ("Nintendo 64 Pikachu Edition Blue Yellow", "n64-pikachu"),
    # fix round 2: drive or box contents named after a console word
    ("Xbox 360 Halo 3 Special Edition Console 20GB HDD", "xbox-360-halo-3-special-edition"),
    ("Xbox 360 Halo Reach Limited Edition Console 250GB HDD",
     "xbox-360-halo-reach-limited-edition"),
    ("PS4 20th Anniversary Edition Console 500GB CUH-1200 HDD", "ps4-20th-anniversary"),
    ("Xbox One X Cyberpunk 2077 Limited Edition Console 1TB HDD", "xbox-one-x-cyberpunk-2077"),
    ("Xbox 360 Halo 3 Special Edition Console CIB Box Manual", "xbox-360-halo-3-special-edition"),
    ("Nintendo DS Lite Pikachu Edition Console New Battery", "ds-lite-pikachu"),
    # a known single product with a counted set of games is valued as that product
    ("Xbox 360 Halo 3 Special Edition Console Bundle with 5 games",
     "xbox-360-halo-3-special-edition"),
])
def test_collectible_console(catalog, title, key):
    result = match_single(title, SEED.products)
    assert result is not None and result[0].id == key
    classified = _classify(title, catalog)
    assert classified is not None and (classified.kind, classified.key) == ("single", key)


@pytest.mark.parametrize("title", [
    # games and accessories that share an edition's name
    "Halo 3 Xbox 360 game",
    "Halo 3 Legendary Edition",
    "Halo 3 Legendary Edition Xbox 360",
    "Halo 3 Limited Edition game",
    "Halo 3 Limited Edition Xbox 360",
    "Halo 3 Xbox 360 controller",
    "Xbox 360 Halo 3 Special Edition Wireless Controller",
    "Halo 3 faceplate",
    "Halo 3 ODST Xbox 360 game",
    "Halo 3: ODST Xbox 360 Limited Edition",
    "Xbox 360 Elite 120GB Console Halo 3 ODST Limited Edition Bundle",
    "Xbox 360 Elite 120GB console with Halo 3 Limited Edition",
    "Halo Reach Limited Edition Xbox 360",
    "Halo Reach Legendary Edition Xbox 360 Console Bundle",
    "Xbox 360 Kinect bundle with Kinect Star Wars game",
    "Xbox 360 Star Wars R2-D2 console skin",
    "Original Xbox console with Halo Combat Evolved",
    "Original Xbox Halo 2 Limited Edition",
    "Xbox One X 1TB console with Cyberpunk 2077",
    "PS4 Beyond Good and Evil 20th Anniversary Edition",
    "PS4 Slim 1TB console bundle with Beyond Good and Evil 20th Anniversary Edition",
    "PS4 Pro 500 Million Limited Edition DualShock 4 controller",
    "Hey You Pikachu N64 game with VRS",
    "Nintendo 64 console with Hey You Pikachu",
    "Pokemon X 3DS game",
    "Majora's Mask 3D game",
    "Zelda Majora's Mask 3D Nintendo 3DS XL",
    "New 3DS XL console with Majora's Mask 3D",
    "Game Boy Advance SP Pikachu charger",
    "Pokemon Ruby Groudon GBA SP cartridge",
    "GBA SP Groudon custom shell",
    "Zelda Wind Waker HD Wii U game",
    "Wii U Deluxe 32GB console with Zelda Wind Waker HD",
    "Hello Kitty Dreamcast controller",
    # plain consoles are not limited editions
    "Xbox 360 console",
    "Xbox 360 Slim 250GB console",
    # fix round 1: an edition named after with / and / + is an extra
    "Xbox 360 Elite console with controller, Halo 3 Limited Edition game",
    "Xbox 360 S 250GB console Halo Reach Edition",
    "Hey You Pikachu Nintendo 64",
    # fix round 1: parts and accessories named after an edition
    "Halo 3 Special Edition Xbox 360 Hard Drive 20GB",
    "Halo 3 Spartan Edition Xbox 360 Hard Drive Green",
    "Xbox 360 Halo 3 Special Edition 20GB HDD",
    "Xbox 360 Halo 3 Special Edition Power Supply",
    "Halo 3 Special Edition Xbox 360 Manual",
    "Halo 3 Special Edition Xbox 360 Console Manual and Inserts",
    "Xbox 360 Halo 3 Special Edition Gold Wireless Controller Charger",
    "Xbox 360 Halo 3 Special Edition Console Parts Motherboard",
    "Halo Reach Limited Edition Xbox 360 250GB Hard Drive",
    "Xbox 360 Star Wars R2-D2 Limited Edition 320GB Hard Drive",
    "Xbox 360 Star Wars R2-D2 Limited Edition Power Supply",
    "Xbox 360 Star Wars R2D2 Kinect Sensor Limited Edition",
    "Xbox One X Cyberpunk 2077 Limited Edition 1TB Hard Drive",
    "PS4 20th Anniversary Edition 500GB Hard Drive",
    "Game Boy Advance SP Pikachu AC Adapter",
    "Nintendo DS Lite Pikachu Battery",
    "DS Lite Dialga Palkia Replacement Hinge",
    "Nintendo DS Lite Dialga Palkia Manual and Inserts",
    "Pokemon Groudon GBA SP Box and Manual",
    "New 3DS XL Majora's Mask Edition Replacement Top Shell",
    "New 3DS XL Pikachu Edition Battery Cover",
    "GBA SP Kyogre Edition Battery Cover",
    "New Nintendo 3DS XL Majora's Mask faceplates",
    "New 3DS XL Majora's Mask Edition Charger",
    "3DS Zelda 25th Anniversary Edition AR Cards",
    "Zelda 25th Anniversary 3DS Bundle Box Insert",
    "Zelda 25th Anniversary Symphony CD 3DS",
    # fix round 2: a console word right before the part names the part ("console manual")
    "Xbox 360 Halo 3 Special Edition Console Manual",
    "Xbox 360 Halo 3 Special Edition Console Box and Manual",
])
def test_collectible_console_no_match(catalog, title):
    assert match_single(title, SEED.products) is None
    assert _classify(title, catalog) is None


def test_collectible_console_lot_with_games_is_not_one_console(catalog):
    # A counted lot of edition consoles with games is still skipped, not valued as one.
    assert _classify("Lot of 3 Xbox 360 Halo 3 Special Edition consoles with 10 games",
                     catalog) is None


# Final review: broken single items, and parts sold on their own
@pytest.mark.parametrize("title", [
    "EVGA RTX 3080 FTW3 10GB Non-Working",
    "RTX 4070 Ti not tested",
    "RTX 3090 Founders no display, artifacting",
    "RTX 4080 Super does not work",
    "i9-13900K CPU - Dead, no post",
    "RTX 4090 Founders Edition doesn't work",
    "RTX 3080 10GB no power",
    "Ryzen 7 7800X3D won't post",
    "RTX 4070 defective",
    "RTX 3090 faulty, needs repair",
    "RTX 4080 for repair",
    "Xbox 360 Halo 3 Special Edition Console 20GB Hard Drive Only",
    "Xbox 360 Halo 3 Special Edition Console HDD only",
    "PS4 20th Anniversary Edition Console Shell Only",
])
def test_broken_or_part_only_singles_rejected(title):
    assert match_single(title, SEED.products) is None


@pytest.mark.parametrize("title, key", [
    ("RTX 3080 10GB tested working", "rtx-3080-10gb"),
    ("Xbox 360 Halo 3 Special Edition Console 20GB HDD", "xbox-360-halo-3-special-edition"),
])
def test_working_singles_still_match(title, key):
    result = match_single(title, SEED.products)
    assert result is not None and result[0].id == key


def test_no_seed_product_name_hits_an_exclude():
    for p in SEED.products:
        result = match_single(p.name, SEED.products)
        assert result is not None and result[0].id == p.id, p.id

@pytest.mark.parametrize('title', [
    'Microsoft Xbox 360 Halo 3 Special Edition Console DVD Drive Bezel + Eject',
    'Hori Fighting Stick PS5 30th Anniversary Controller',
    'PS5 PRO SLIM 30th Anniversary Edition RGB Cooling Fan For SONY PlayStation 5',
    'Retro Black Gloss Ps5 Buttons 30th Anniversary Logo',
    'HautePad P12 Pro Wired PS5 PC Arcade Fighting Controller Anniversary Edition',
    'PS5 30th Anniversary DualSense replacement buttons set',
    'Lot of 10 PS5 30th Anniversary controller buttons',
    'PS5 30th Anniversary controller silicone protective case',
    'Xbox 360 Halo 3 Special Edition replacement shell',
    'PS5 30th Anniversary arcade fight stick controller',
])
def test_accessories_and_arcade_controls_never_enter_any_listing_path(title, catalog):
    assert _classify(title, catalog) is None
    assert match_single(title, catalog.products) is None

@pytest.mark.parametrize('title', [
    'Xbox 360 Halo 3 Special Edition Console Working with controller',
    'Sony PS2 PlayStation 2 Slim Sakura Pink Console Tested',
    'Sony PlayStation 2 Ocean Blue Console SCPH-37000 Tested',
    'PS5 DualSense 30th Anniversary Limited Edition Wireless Controller',
])
def test_collectible_hardware_remains_eligible(title, catalog):
    result = _classify(title, catalog)
    assert result is not None and result.kind == 'single'

@pytest.mark.parametrize('title', [
 'Microsoft Xbox 360 S Slim Black Console Halo 4 NCAA Football 12 3 Game Bundle',
 'Microsoft Xbox 360 S Chrome Black Console Bundle Halo 4 Kinect Controller',
 'Microsoft Original Limited Green Edition Xbox Console Tested Rare non Halo',
])
def test_ordinary_consoles_with_franchise_games_are_not_premium_edition_hardware(title,catalog):
    assert _classify(title,catalog) is None
