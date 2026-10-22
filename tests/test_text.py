import re

import pytest

from dealfinder.text import LOT_WORDS, normalize, parse_quantity, pick_most_specific

MASKS = [
    r"\b(ps2|playstation 2)\b",
    r"\bxbox 360\b",
    r"\b(nintendo )?switch\b",
    r"\b(nintendo )?switch 2\b",
    r"\b(ps5|playstation 5)\b",
]


@pytest.mark.parametrize(
    "title, expected",
    [
        ("lot of 5 ps2 consoles for parts", 5),
        ("5x xbox 360 controllers", 5),
        ("ps2 slim x3 untested", 3),
        ("(12) nintendo ds games", 12),
        ("qty 4 rtx 3060", 4),
        ("five ps2 consoles", 5),
        ("20 gamecube games", 20),
        ("playstation 2 console slim", 1),
        ("xbox 360 controller white", 1),
        ("rtx 3080 pcie 4.0 x16 12gb", 1),
        ("rx 6800 2x hdmi 3x fans", 1),
        ("marvels spider man 2 ps5 bundle", 1),
        ("box of video games", None),
        ("huge lot ps2 games", None),
        ("3 nintendo switch 2 consoles", 3),
        ("spider man 2 ps5 console bundle", 1),
        ("lot 5 nintendo 3ds xl systems", 5),
    ],
)
def test_quantity_forms(title, expected):
    assert parse_quantity(title, MASKS) == expected


PS4 = r"\b(ps4|playstation 4)\b"


@pytest.mark.parametrize(
    "raw, extra_masks, expected",
    [
        ("MSI RTX 3080 10GB PCI Express 4.0 x16 Graphics Card", [], 1),
        ("EVGA GTX 1080 Ti PCIe Gen3 x16", [], 1),
        ("RTX 3070 PCIe 16x", [], 1),
        ("ASUS RTX 3080 Ti 12GB 2x 8-Pin", [], 1),
        ("Sony PS5 Disc Console Bundle with 2 Controllers", [], 1),
        ("Xbox Series X 1TB Console w/ 2 Controllers and 3 Games", [], 1),
        ("Nintendo Switch OLED w/ 3 Games", [], 1),
        ("PS4 Slim 1TB + 10 Games", [PS4], 1),
        ("Nintendo 3DS XL with 5 Games", [], 1),
        ("Sony DualShock 4 PS4 Controllers", [PS4], 1),
        ("Ryzen 5 3600 Processors", [], 1),
        ("Sega Genesis 32X Console", [], 1),
        ("lot of 5 ps2 consoles with 3 controllers", [], 5),
        ("Lot of 3 w/ cables", [], 3),
        ("Xbox 360 Lot of 4 w/ AC Adapters", [], 4),
        ("Huge lots of PS2 games", [], None),
        ("Lot of 1,200 Pokemon Cards", [], None),
        ("MSI GeForce RTX 4070 Ventus 3X 12G OC", [], 1),
        ("Gigabyte RTX 3080 Windforce 3X 10GB", [], 1),
        ("MSI RTX 3060 Ventus 2X 12G OC", [], 1),
        ("ASUS TUF Gaming RTX 4070 Ti OC 3x fans", [], 1),
        ("Lot of 3 MSI RTX 3060 Ventus 2X 12G", [], 3),
        ("4x MSI RTX 3060 Ventus 2X", [], 4),
    ],
)
def test_quantity_raw_titles(raw, extra_masks, expected):
    assert parse_quantity(normalize(raw), MASKS + extra_masks) == expected


def test_quantity_out_of_range_hit_is_ignored():
    assert parse_quantity("lot of 500 ps2 consoles", MASKS) is None
    assert parse_quantity("lot of 500 ps2 (12)", MASKS) == 12
    assert parse_quantity("xbox one controllers", MASKS) == 1


def test_lot_words():
    assert LOT_WORDS == ("lot", "lots", "box of", "collection", "bulk", "wholesale")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("RTX4070Ti SUPER 16GB", "rtx 4070 ti super 16gb"),
        ("Ryzen 7 7800X3D", "ryzen 7 7800x3d"),
        ("Radeon RX 7900XTX", "radeon rx 7900 xtx"),
        ("RTX 4070TiSuper", "rtx 4070 ti super"),
        ("RX 9070XT / 7900GRE", "rx 9070 xt 7900 gre"),
        ("Intel i7-13700KF & Ryzen 9 7900X", "intel i7 13700kf and ryzen 9 7900x"),
        ("Xbox-360,  Controller | Black™", "xbox 360 controller black"),
        ("RTX 3080 PCIe 4.0 x16", "rtx 3080 pcie 4.0 x16"),
        ("PS2 Games 1/2 Price", "ps2 games 1/2 price"),
        ("Lot of 3 w/ cables, w/o box", "lot of 3 with cables without box"),
        ("Lot of 1,200 Pokemon Cards", "lot of 1,200 pokemon cards"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def _m(pattern, text):
    return re.search(pattern, text)


def test_pick_most_specific_contained_span_wins():
    text = "rtx 4070 ti super"
    matches = [("4070", _m(r"rtx 4070", text)), ("4070ts", _m(r"rtx 4070 ti super", text))]
    assert pick_most_specific(matches) == "4070ts"


def test_pick_most_specific_disjoint_spans_ambiguous():
    text = "rtx 3080 and rtx 3090"
    matches = [("3080", _m(r"rtx 3080", text)), ("3090", _m(r"rtx 3090", text))]
    assert pick_most_specific(matches) is None


def test_pick_most_specific_empty():
    assert pick_most_specific([]) is None


def test_pick_most_specific_same_object_counts_once():
    text = "ps2 console and ps2 slim"
    matches = [("ps2", m) for m in re.finditer(r"ps2", text)]
    assert pick_most_specific(matches) == "ps2"
