import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx

from dealfinder.sources.base import HttpClient
from dealfinder.sources.ebay import (
    ITEM_URL,
    SEARCH_URL,
    TOKEN_URL,
    EbaySource,
    parse_search,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ebay"
SEARCH = json.loads((FIXTURES / "search.json").read_text())
ITEM = json.loads((FIXTURES / "item.json").read_text())
T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def make_source(now=lambda: T0):
    http = HttpClient(sleep=lambda s: None, clock=lambda: 0.0)
    return EbaySource(http, "cid", "secret", now)


def test_parse_search_fields():
    listings = parse_search(SEARCH)
    assert len(listings) == 2
    a = listings[0]
    assert a.source == "ebay"
    assert a.source_id == "v1|110000000001|0"
    assert a.title == "NVIDIA GeForce RTX 4070 Founders Edition 12GB"
    assert a.url == "https://www.ebay.com/itm/110000000001"
    assert a.shipping == 12.99
    assert a.shipping_offered is True
    assert a.image_urls == [
        "https://i.ebayimg.com/images/g/aaa/s-l225.jpg",
        "https://i.ebayimg.com/images/g/bbb/s-l225.jpg",
        "https://i.ebayimg.com/images/g/ccc/s-l225.jpg",
    ]
    assert a.is_auction is True
    assert a.end_time == datetime(2026, 10, 25, 18, 30, tzinfo=timezone.utc)
    assert a.end_time.utcoffset() == timedelta(0)
    assert a.bid_count == 7
    assert a.condition_id == "3000"
    b = listings[1]
    assert b.is_auction is False
    assert b.end_time is None
    assert b.bid_count == 0
    assert b.image_urls == ["https://i.ebayimg.com/images/g/ddd/s-l225.jpg"]


def test_auction_uses_current_bid():
    listings = parse_search(SEARCH)
    assert listings[0].price == 312.50
    assert listings[1].price == 399.99


def test_auction_without_current_bid_uses_price():
    data = {
        "itemSummaries": [
            {
                "itemId": "x",
                "title": "t",
                "price": {"value": "10.00", "currency": "USD"},
                "buyingOptions": ["AUCTION"],
                "itemWebUrl": "https://www.ebay.com/itm/x",
            }
        ]
    }
    assert parse_search(data)[0].price == 10.0


def test_calculated_shipping_is_none():
    b = parse_search(SEARCH)[1]
    assert b.shipping is None
    assert b.shipping_offered is True


def test_missing_price_skipped():
    ids = [l.source_id for l in parse_search(SEARCH)]
    assert "v1|110000000003|0" not in ids


def test_empty_response():
    assert parse_search({}) == []


@respx.mock
def test_search_request_shape():
    token = respx.post(TOKEN_URL).respond(
        200, json={"access_token": "tok", "expires_in": 7200}
    )
    search = respx.get(SEARCH_URL).respond(200, json=SEARCH)
    listings = make_source().search("rtx 4070")
    assert len(listings) == 2
    req = search.calls[0].request
    assert req.url.params["q"] == "rtx 4070"
    assert req.url.params["limit"] == "200"
    assert req.url.params["filter"] == (
        "buyingOptions:{AUCTION|FIXED_PRICE},deliveryOptions:{SHIP_TO_HOME},"
        "itemLocationCountry:US"
    )
    assert req.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
    assert req.headers["Authorization"] == "Bearer tok"
    treq = token.calls[0].request
    assert treq.headers["Authorization"].startswith("Basic ")
    body = treq.content.decode()
    assert "grant_type=client_credentials" in body
    assert "scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope" in body


@respx.mock
def test_fixed_price_only_filter():
    respx.post(TOKEN_URL).respond(200, json={"access_token": "tok", "expires_in": 7200})
    search = respx.get(SEARCH_URL).respond(200, json=SEARCH)
    make_source().search("rtx 4070", fixed_price_only=True)
    assert search.calls[0].request.url.params["filter"].startswith("buyingOptions:{FIXED_PRICE},")


@respx.mock
def test_token_cached():
    token = respx.post(TOKEN_URL).respond(
        200, json={"access_token": "tok", "expires_in": 7200}
    )
    respx.get(SEARCH_URL).respond(200, json=SEARCH)
    src = make_source()
    src.search("a")
    src.search("b")
    assert token.call_count == 1


@respx.mock
def test_token_refreshed_after_expiry():
    token = respx.post(TOKEN_URL).respond(
        200, json={"access_token": "tok", "expires_in": 7200}
    )
    respx.get(SEARCH_URL).respond(200, json=SEARCH)
    clock = {"now": T0}
    src = make_source(lambda: clock["now"])
    src.search("a")
    clock["now"] = T0 + timedelta(seconds=7200 - 61)
    src.search("b")
    assert token.call_count == 1
    clock["now"] = T0 + timedelta(seconds=7200 - 60)
    src.search("c")
    assert token.call_count == 2


@respx.mock
def test_photos_from_item_detail():
    respx.post(TOKEN_URL).respond(200, json={"access_token": "tok", "expires_in": 7200})
    detail = respx.get(url__startswith="https://api.ebay.com/buy/browse/v1/item/").respond(
        200, json=ITEM
    )
    photos = make_source().photos("v1|110000000001|0", ["https://old/1.jpg"])
    assert photos == [
        "https://i.ebayimg.com/images/g/aaa/s-l1600.jpg",
        "https://i.ebayimg.com/images/g/bbb/s-l1600.jpg",
        "https://i.ebayimg.com/images/g/ccc/s-l1600.jpg",
        "https://i.ebayimg.com/images/g/ddd/s-l1600.jpg",
    ]
    assert detail.calls[0].request.url.raw_path.decode() == "/buy/browse/v1/item/v1%7C110000000001%7C0"
    assert ITEM_URL.format(item_id="x") == "https://api.ebay.com/buy/browse/v1/item/x"


@respx.mock
def test_photos_capped_at_eight():
    respx.post(TOKEN_URL).respond(200, json={"access_token": "tok", "expires_in": 7200})
    item = {
        "image": {"imageUrl": "u0"},
        "additionalImages": [{"imageUrl": f"u{i}"} for i in range(1, 12)],
    }
    respx.get(url__startswith="https://api.ebay.com/buy/browse/v1/item/").respond(200, json=item)
    assert make_source().photos("abc", []) == [f"u{i}" for i in range(8)]
