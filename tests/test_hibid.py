import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx
import pytest

from dealfinder.sources.base import HttpClient, SourceError
from dealfinder.sources.hibid import (
    GRAPHQL_URL,
    LOT_SEARCH_QUERY,
    HiBidSource,
    parse_search,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hibid"
SEARCH = json.loads((FIXTURES / "search.json").read_text())
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def lots():
    return SEARCH["data"]["lotSearch"]["pagedResults"]["results"]


def wrap(results):
    return {"data": {"lotSearch": {"pagedResults": {"results": results}}}}


def make_source():
    http = HttpClient(sleep=lambda s: None, clock=lambda: 0.0)
    return HiBidSource(http, lambda: NOW)


def test_hibid_parse_fields():
    listings = parse_search(SEARCH, NOW)
    assert len(listings) == 3  # the closed lot is skipped
    a = listings[0]
    assert a.source == "hibid"
    assert a.source_id == "191000001"
    assert a.title == "Sony PlayStation 4 Pro 1TB Console with Controller"
    assert a.url == "https://hibid.com/lot/191000001"
    assert a.price == 85.0
    assert a.shipping is None
    assert a.is_auction is True
    assert a.bid_count == 6
    assert a.end_time == NOW + timedelta(hours=2)
    assert a.end_time.utcoffset() == timedelta(0)
    assert a.image_urls == [
        "https://cdn.hibid.com/img/191000001/a.jpg",
        "https://cdn.hibid.com/img/191000001/b.jpg",
    ]


def test_hibid_min_bid_when_no_bids():
    listings = parse_search(SEARCH, NOW)
    assert listings[1].source_id == "191000002"
    assert listings[1].price == 5.0
    assert listings[1].bid_count == 0


def test_hibid_premium_percent():
    listings = parse_search(SEARCH, NOW)
    assert listings[0].buyer_premium_pct == 0.15
    assert listings[1].buyer_premium_pct == 0.18
    assert listings[2].buyer_premium_pct is None


def test_hibid_closed_skipped():
    ids = [x.source_id for x in parse_search(SEARCH, NOW)]
    assert "191000003" not in ids


def test_hibid_no_shipping_flagged():
    listings = parse_search(SEARCH, NOW)
    # lot False + auction False -> pickup only; auction True or lot True -> ships
    assert [x.shipping_offered for x in listings] == [True, False, True]


def test_hibid_photos_capped_and_missing_ok():
    pics = [{"fullSizeLocation": f"https://cdn.hibid.com/{i}.jpg"} for i in range(10)]
    lot = {**lots()[0], "pictures": pics + [{"fullSizeLocation": None}, {}]}
    (listing,) = parse_search(wrap([lot]), NOW)
    assert len(listing.image_urls) == 8
    assert listing.image_urls[0] == "https://cdn.hibid.com/0.jpg"
    assert parse_search(SEARCH, NOW)[2].image_urls == []


def test_hibid_malformed_lots_skipped():
    good = lots()[0]
    state = good["lotState"]
    bad = [
        {**good, "id": None},
        {**good, "lead": ""},
        {k: v for k, v in good.items() if k != "lead"},
        {**good, "lotState": None},
        {**good, "lotState": {**state, "highBid": "lots"}},
        {**good, "lotState": {**state, "highBid": 0, "minBid": None}},
        {**good, "lotState": {**state, "timeLeftSeconds": "soon"}},
        {**good, "auction": {"buyerPremium": "high"}},
        "garbage",
    ]
    listings = parse_search(wrap(bad + [good]), NOW)
    assert [x.source_id for x in listings] == ["191000001"]
    assert parse_search({}, NOW) == []
    assert parse_search({"data": None, "errors": [{"message": "x"}]}, NOW) == []


def test_hibid_naive_now_treated_as_utc():
    (listing,) = parse_search(wrap([lots()[0]]), NOW.replace(tzinfo=None))
    assert listing.end_time == NOW + timedelta(hours=2)
    assert listing.end_time.tzinfo is not None


@respx.mock
def test_hibid_search_posts_graphql():
    route = respx.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json=SEARCH))
    listings = make_source().search("playstation")
    assert len(listings) == 3
    body = json.loads(route.calls[0].request.content)
    assert body["query"] == LOT_SEARCH_QUERY
    assert body["variables"]["searchText"] == "playstation"
    assert listings[0].end_time == NOW + timedelta(hours=2)


def test_hibid_photos_returns_known():
    assert make_source().photos("191000001", ["a", "b"]) == ["a", "b"]


@respx.mock
def test_graphql_errors_are_reported_instead_of_becoming_empty_results():
    respx.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json={
        "data": None, "errors": [{"message": "Unknown argument pageNumber"}]}))
    with pytest.raises(SourceError, match="Unknown argument pageNumber"):
        make_source().search("rtx")
