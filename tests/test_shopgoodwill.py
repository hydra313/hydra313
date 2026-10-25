import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from dealfinder.sources.base import HttpClient, SourceBlocked
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
        "lowPrice": 0,
        "highPrice": 999999,
        "searchClosedAuctions": False,
        "searchNoPickupOnly": True,
        "sortColumn": 1,
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


@respx.mock
def test_sgw_photos_without_detail_images_keep_known():
    respx.get(ITEM_URL.format(item_id="240000001")).mock(
        return_value=httpx.Response(200, json={"imageUrlString": None}))
    assert make_source().photos("240000001", ["thumb"]) == ["thumb"]


@respx.mock
def test_sgw_photos_invalid_json_keep_known():
    respx.get(ITEM_URL.format(item_id="240000001")).mock(
        return_value=httpx.Response(200, text="<html>not json</html>"))
    assert make_source().photos("240000001", ["thumb"]) == ["thumb"]


@respx.mock
def test_sgw_photos_block_is_raised():
    respx.get(ITEM_URL.format(item_id="240000001")).mock(return_value=httpx.Response(429))
    with pytest.raises(SourceBlocked):
        make_source().photos("240000001", ["thumb"])


def test_live_search_response_and_image_paths():
    data = json.loads((FIXTURES / "live_search.json").read_text(encoding="utf-8"))
    listings = parse_search(data)
    assert len(listings) == 2
    assert listings[0].source_id == "280831591"
    assert listings[0].price == 156.55
    assert listings[0].end_time == datetime(2026, 10, 24, 1, 20, tzinfo=timezone.utc)
    assert listings[0].image_urls[0].startswith("https://shopgoodwillimages.azureedge.net/production/339/Item/")
    assert all("\\" not in u for listing in listings for u in listing.image_urls)


def test_live_detail_resolves_relative_photos():
    data = json.loads((FIXTURES / "live_item.json").read_text(encoding="utf-8"))
    photos = parse_item_detail(data)
    assert len(photos) == 4
    assert photos[0] == ("https://shopgoodwillimages.azureedge.net/production/339/Item/"
                         "2026-10-16/00c86d7c-d260-4d4f-a78b-07c9daca0478anta_10161.jpg")
    assert all("\\" not in p for p in photos)


def test_buy_now_uses_buy_now_price_and_is_not_an_auction():
    good = SEARCH["searchResults"]["items"][0]
    data = {"searchResults": {"items": [{**good, "listingType": 1,
                                          "currentPrice": 0, "buyNowPrice": 175}]}}
    (listing,) = parse_search(data)
    assert listing.price == 175
    assert listing.is_auction is False


def test_detail_discards_unsafe_or_unresolvable_photo_paths():
    assert parse_item_detail({"imageUrlString": "relative.jpg;javascript:alert(1)"}) == []
    assert parse_item_detail({"imageServer": "https://images.example.com/",
                              "imageUrlString": "relative.jpg;https://images.example.com/full.jpg"}) == [
        "https://images.example.com/relative.jpg", "https://images.example.com/full.jpg"]

@respx.mock
def test_search_follows_full_pages_deduplicates_and_stops_at_empty_page():
    item=SEARCH['searchResults']['items'][0]
    pages=[]
    def respond(request):
        page=json.loads(request.content)['page'];pages.append(page)
        items=[{**item,'itemId':str(i+240000000)} for i in range(40)] if page==1 else [{**item,'itemId':'240000999'}] if page==2 else []
        return httpx.Response(200,json={'searchResults':{'items':items}})
    respx.post(SEARCH_URL).mock(side_effect=respond)
    results=make_source().search('limited edition console')
    assert pages == [1,2] and len(results)==41
