import pytest

from dealfinder.matcher import detect_condition, match_single
from dealfinder.models import Product
from dealfinder.text import normalize


def _p(pid, category, match, **kw):
    return Product(id=pid, category=category, name=pid, ebay_query=pid, match=match, **kw)


PRODUCTS = [
    _p("4070-ti", "gpu", r"\b4070 ti\b"),
    _p("4070-ti-super", "gpu", r"\b4070 ti super\b"),
    _p("3080", "gpu", r"\b3080\b"),
    _p("3090", "gpu", r"\b3090\b"),
    _p("4090", "gpu", r"\b4090\b"),
    _p("13900k", "cpu", r"\b13900k\b"),
    _p("ps5", "console", r"\b(ps5|playstation 5)\b"),
    _p("dualsense-30th", "controller", r"\bdualsense\b.*\b30th anniversary\b"),
]


def _id(title, products=PRODUCTS, condition_id=None):
    r = match_single(title, products, condition_id)
    return None if r is None else r[0].id


def test_ti_super_beats_ti():
    assert _id("MSI RTX 4070 Ti Super 16GB") == "4070-ti-super"


def test_plain_ti():
    assert _id("RTX 4070 Ti 12GB") == "4070-ti"


def test_ambiguous_rejected():
    assert _id("RTX 3080 or 3090 trade") is None


def test_no_match_rejected():
    assert _id("RTX 2060 6GB") is None


@pytest.mark.parametrize(
    "phrase",
    [
        "for parts", "not working", "untested", "as-is", "box only", "empty box",
        "bent pins", "engineering sample", "ES", "read description",
    ],
)
def test_global_excludes(phrase):
    assert _id(f"RTX 3080 10GB {phrase}") is None


@pytest.mark.parametrize(
    "phrase",
    ["parts only", "broken", "as is", "no gpu", "no cpu", "QS", "bent pin", "engineering samples"],
)
def test_more_global_excludes(phrase):
    assert _id(f"RTX 3080 10GB {phrase}") is None


def test_es_qs_only_as_whole_words():
    assert _id("ASUS RTX 3080 Strix Gaming Limited Specs Series Premium") == "3080"
    assert _id("RTX 3080 Les Paul Qsync Edition") == "3080"


@pytest.mark.parametrize(
    "title",
    [
        "RTX 4090 laptop",
        "RTX 3080 waterblock",
        "i9 13900K mobo combo",
        "DualSense 30th Anniversary faceplate only",
    ],
)
def test_category_excludes(title):
    assert _id(title) is None


@pytest.mark.parametrize(
    "title",
    [
        "RTX 3080 water block",
        "RTX 3080 backplate",
        "RTX 3080 bundle",
        "RTX 3080 prebuilt",
        "RTX 3080 gaming pc",
        "RTX 3080 fan only",
        "RTX 3080 cooler only",
        "i9 13900K combo",
        "i9 13900K bundle",
        "i9 13900K cooler only",
        "PS5 skin",
        "PS5 charging station",
        "PS5 stand only",
    ],
)
def test_more_category_excludes(title):
    assert _id(title) is None


def test_category_excludes_are_per_category():
    # "combo" only excludes CPUs; "bundle" is fine for consoles
    assert _id("PS5 combo") == "ps5"
    assert _id("i9 13900K") == "13900k"


def test_console_bundle_allowed():
    assert _id("PS5 Spider-Man 2 Limited Edition Bundle") == "ps5"


def test_product_exclude():
    prods = [_p("3080", "gpu", r"\b3080\b", exclude=(r"\b12gb\b",))]
    assert _id("RTX 3080 12GB", prods) is None
    assert _id("RTX 3080 10GB", prods) == "3080"


def test_disabled_products_ignored():
    prods = [_p("3080", "gpu", r"\b3080\b", enabled=False)]
    assert _id("RTX 3080", prods) is None
    prods.append(_p("3080-b", "gpu", r"\b3080\b"))
    assert _id("RTX 3080", prods) == "3080-b"


def test_disabled_product_does_not_cause_ambiguity():
    prods = [_p("3090", "gpu", r"\b3090\b", enabled=False), _p("3080", "gpu", r"\b3080\b")]
    assert _id("RTX 3080 or 3090", prods) == "3080"


def test_condition():
    r = match_single("RTX 4090 factory sealed", PRODUCTS)
    assert r is not None and r[1] == "new"
    r = match_single("RTX 4090", PRODUCTS, "3000")
    assert r is not None and r[1] == "used"
    assert match_single("RTX 4090", PRODUCTS, "7000") is None
    r = match_single("RTX 4090", PRODUCTS, "1000")
    assert r is not None and r[1] == "new"


@pytest.mark.parametrize(
    "title, expected",
    [
        ("rtx 4090 sealed", "new"),
        ("ps5 new in box", "new"),
        ("ps5 nib", "new"),
        ("ps5 bnib", "new"),
        ("ps5 brand new", "new"),
        ("ps5 factory sealed", "new"),
        ("ps5 disc edition", "used"),
        ("ps5 renewed", "used"),
        ("ps5 unsealed", "used"),
    ],
)
def test_detect_condition_from_title(title, expected):
    assert detect_condition(normalize(title), None) == expected


@pytest.mark.parametrize(
    "cid, expected",
    [("1000", "new"), ("1500", "new"), ("7000", None), ("3000", "used"), ("2500", "used")],
)
def test_detect_condition_ebay_id_wins_over_title(cid, expected):
    assert detect_condition("rtx 4090", cid) == expected
    assert detect_condition("rtx 4090 factory sealed", cid) == expected


def test_explicit_used_condition_cannot_be_valued_as_brand_new():
    assert detect_condition(normalize('Xbox Series X Halo Infinite Lightly Used 70 hrs'),'1000') == 'used'
    assert detect_condition(normalize('Xbox Series X Halo Infinite Not Used Factory Sealed'),'1000') == 'new'
