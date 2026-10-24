from datetime import date
from pathlib import Path

import httpx
import pytest

from dealfinder.sources.base import HttpClient
from dealfinder.sources.ebay_sold import SoldFetcher, parse_sold, sold_url

FIXTURES = Path(__file__).parent / "fixtures" / "ebay_sold"
LEGACY = (FIXTURES / "s_item.html").read_text()
CARD = (FIXTURES / "s_card.html").read_text()

@pytest.mark.parametrize("html", [LEGACY, CARD], ids=["legacy", "card"])
def test_price_includes_shipping(html):
    comps = parse_sold(html)
    gpu = next(c for c in comps if "RTX 4070" in c.title)
    assert gpu.price == 112.50


def test_parse_legacy_layout():
    comps = parse_sold(LEGACY)
    assert [c.title for c in comps] == [
        "NVIDIA GeForce RTX 4070 Founders Edition 12GB",
        "Sony PlayStation 5 Console Disc Edition",
        "AMD Ryzen 7 5800X3D Processor",
        "Nintendo Switch OLED Console White",
    ]
    assert [c.price for c in comps] == [112.50, 1234.56, 221.00, 175.00]
    assert comps[0].url == "https://www.ebay.com/itm/110000000002?hash=item2b"
    assert comps[1].price == 1234.56  # "Free delivery" adds nothing
    assert comps[3].price == 175.00  # no shipping element at all


def test_parse_card_layout():
    comps = parse_sold(CARD)
    assert [c.title for c in comps] == [
        "NVIDIA GeForce RTX 4070 Founders Edition 12GB",
        "Sony PlayStation 5 Console Disc Edition",
        "AMD Ryzen 7 5800X3D Processor",
        "Nintendo Switch OLED Console White",
    ]
    assert [c.price for c in comps] == [112.50, 1234.56, 221.00, 175.00]
    assert comps[0].url == "https://www.ebay.com/itm/210000000002?itmmeta=02"
    assert comps[1].price == 1234.56
    assert comps[3].price == 175.00


@pytest.mark.parametrize("html", [LEGACY, CARD], ids=["legacy", "card"])
def test_skips_placeholder_and_ranges(html):
    titles = [c.title for c in parse_sold(html)]
    assert not any("Shop on eBay" in t for t in titles)
    assert not any("Super Nintendo" in t for t in titles)  # price range
    assert not any("Controller" in t for t in titles)  # unparseable price
    assert len(titles) == 4


@pytest.mark.parametrize("html", [LEGACY, CARD], ids=["legacy", "card"])
def test_sold_date_and_condition_parsed(html):
    comps = parse_sold(html)
    assert [c.sold_date for c in comps] == [
        date(2026, 10, 3),  # double-space "Sold  Oct 3, 2026"
        date(2026, 9, 28),
        None,  # unparseable date: the item is kept
        date(2026, 10, 12),
    ]
    assert [c.condition_label for c in comps] == [
        "Pre-Owned",
        "Open Box",
        "Pre-Owned",
        "Used",
    ]


@pytest.mark.parametrize("html", [LEGACY, CARD], ids=["legacy", "card"])
def test_title_prefix_and_suffix_stripped(html):
    for c in parse_sold(html):
        assert not c.title.startswith("New Listing")
        assert "Opens in a new window" not in c.title


def test_malformed_item_does_not_discard_page():
    html = (
        '<li class="s-item"></li>'
        '<li class="s-card"><div class="s-card__title"></div></li>'
        + LEGACY
    )
    assert len(parse_sold(html)) == 4


def test_sold_url_encodes_query():
    url = sold_url("rtx 4070 ti & super")
    assert url == (
        "https://www.ebay.com/sch/i.html?_nkw=rtx+4070+ti+%26+super"
        "&LH_Sold=1&LH_Complete=1&LH_PrefLoc=1&_ipg=240"
    )


def test_fetcher_requests_sold_url_and_parses():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=CARD, headers={"content-type": "text/html"})

    http = HttpClient(
        transport=httpx.MockTransport(handler), sleep=lambda s: None, clock=lambda: 0.0
    )
    comps = SoldFetcher(http).fetch("rtx 4070")
    assert seen == [sold_url("rtx 4070")]
    assert len(comps) == 4
