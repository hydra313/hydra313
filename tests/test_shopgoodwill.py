import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx

from dealfinder.sources.base import HttpClient
from dealfinder.sources.shopgoodwill import (
    ITEM_URL,
    SEARCH_URL,
    ShopGoodwillSource,
    build_search_body,
    parse_item_detail,
    parse_search,
)

FIXTURES = Path(__file__).parent / "fixtures" / "shopgoodwill"
SEARCH = json.loads((FIXTURES / "search.json").read_text())
ITEM = json.loads((FIXTURES / "item.json").read_text())


def make_source():
    return ShopGoodwillSource(HttpClient(sleep=lambda s: None, clock=lambda: 0.0))


def test_sgw_parse_fields():
    listings = parse_search(SEARCH)
    assert len(listings) == 4
    a = listings[0]
    assert a.source == "shopgoodwill"
    assert a.source_id == "240000001"
    assert a.title == "NVIDIA GeForce RTX 3070 8GB Graphics Card Tested"
    assert a.url == "https://shopgoodwill.com/item/240000001"
    assert a.price == 187.5
    assert a.shipping is None
    assert a.shipping_offered is True
    assert a.image_urls == ["https://images.shopgoodwill.com/240000001/thumb.jpg"]
    assert a.is_auction is True
    assert a.bid_count == 9
    assert a.buyer_premium_pct is None
    assert listings[3].image_urls == []


def test_sgw_end_time_pacific_to_utc():
    listings = parse_search(SEARCH)
    assert listings[0].end_time == datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)
    assert listings[0].end_time.utcoffset() == timedelta(0)
    # after DST ends on 2026-11-01 Pacific is UTC-8
    assert listings[2].end_time == datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)


def test_sgw_pickup_only_flagged():
    listings = parse_search(SEARCH)
    assert [x.shipping_offered for x in listings] == [True, False, False, True]


def test_sgw_item_photos():
    photos = parse_item_detail(ITEM)
    assert len(photos) == 8
    assert photos[0] == "https://images.shopgoodwill.com/240000001/1.jpg"
    assert photos[7] == "https://images.shopgoodwill.com/240000001/8.jpg"
    assert parse_item_detail({}) == []
    assert parse_item_detail({"imageUrlString": None}) == []


def test_sgw_malformed_items_skipped():
    good = SEARCH["searchResults"]["items"][0]
    bad = [
        {**good, "itemId": None},
        {**good, "title": ""},
        {k: v for k, v in good.items() if k != "title"},
        {**good, "currentPrice": "n/a"},
        {**good, "currentPrice": None},
        {**good, "endTime": "not a date"},
        {**good, "numBids": "many"},
        "garbage",
    ]
    data = {"searchResults": {"items": bad + [good]}}
    listings = parse_search(data)
    assert [x.source_id for x in listings] == ["240000001"]
    assert parse_search({}) == []
    assert parse_search({"searchResults": None}) == []


def test_sgw_build_search_body():
    assert build_search_body("rtx 3070") == {
        "searchText": "rtx 3070",
        "page": 1,
        "pageSize": 40,
        "closedAuctions": False,
        "sortColumn": "1",
        "sortDescending": False,
    }
    assert build_search_body("x", page=3, page_size=10)["page"] == 3


@respx.mock
def test_sgw_search_posts_json():
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH))
    listings = make_source().search("rtx 3070")
    assert len(listings) == 4
    assert json.loads(route.calls[0].request.content) == build_search_body("rtx 3070")


@respx.mock
def test_sgw_photos_fetches_item_detail():
    respx.get(ITEM_URL.format(item_id="240000001")).mock(
        return_value=httpx.Response(200, json=ITEM)
    )
    photos = make_source().photos("240000001", ["thumb"])
    assert len(photos) == 8


@respx.mock
def test_sgw_photos_falls_back_to_known_on_error():
    respx.get(ITEM_URL.format(item_id="240000001")).mock(
        return_value=httpx.Response(404)
    )
    assert make_source().photos("240000001", ["thumb"]) == ["thumb"]
