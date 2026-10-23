from __future__ import annotations

import math
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from dealfinder.models import Listing
from dealfinder.sources.base import HttpClient, SourceBlocked, SourceError

# Unofficial buyer API, checked against live search and detail responses on
# 2026-10-23. Keep request fields and response names here for future site changes.
SEARCH_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"
ITEM_URL = "https://buyerapi.shopgoodwill.com/api/ItemDetail/GetItemDetailModelByItemId/{item_id}"
LISTING_URL = "https://shopgoodwill.com/item/{item_id}"
TIMEZONE = "America/Los_Angeles"
MAX_PHOTOS = 8
IMAGE_SEPARATOR = ";"
RESULTS_PATH = ("searchResults", "items")
FIELD_ID = "itemId"
FIELD_TITLE = "title"
FIELD_PRICE = "currentPrice"
FIELD_BIDS = "numBids"
FIELD_END = "endTime"
FIELD_IMAGE = "imageURL"
FIELD_PICKUP_ONLY = "pickupOnly"
FIELD_DETAIL_IMAGES = "imageUrlString"
FIELD_IMAGE_SERVER = "imageServer"
FIELD_LISTING_TYPE = "listingType"
FIELD_BUY_NOW_PRICE = "buyNowPrice"


def build_search_body(query: str, page: int = 1, page_size: int = 40) -> dict:
    return {
        "searchText": query,
        "page": page,
        "pageSize": page_size,
        "lowPrice": 0,
        "highPrice": 999999,
        "searchClosedAuctions": False,
        "searchNoPickupOnly": True,
        "sortColumn": 1,
        "sortDescending": False,
    }


def _end_time_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(TIMEZONE))
    return dt.astimezone(timezone.utc)


def _image_url(value: str | None, server: str = "") -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(server, value.strip().replace("\\", "/"))
    parsed = urlsplit(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else None


def _parse_item(item: dict) -> Listing:
    item_id = item[FIELD_ID]
    title = item[FIELD_TITLE]
    if item_id in (None, "") or not isinstance(title, str) or not title.strip():
        raise ValueError("item without id or title")
    is_auction = item.get(FIELD_LISTING_TYPE) != 1
    price = float(item[FIELD_PRICE] if is_auction else item[FIELD_BUY_NOW_PRICE])
    if not math.isfinite(price) or price < 0:
        raise ValueError("invalid price")
    image = _image_url(item.get(FIELD_IMAGE))
    source_id = str(item_id)
    return Listing(
        source="shopgoodwill",
        source_id=source_id,
        title=title,
        url=LISTING_URL.format(item_id=source_id),
        price=price,
        shipping=None,
        shipping_offered=not item.get(FIELD_PICKUP_ONLY) and "pickup only" not in title.lower(),
        image_urls=[image] if image else [],
        is_auction=is_auction,
        end_time=_end_time_utc(item.get(FIELD_END)),
        bid_count=int(item.get(FIELD_BIDS) or 0),
    )


def parse_search(data: dict) -> list[Listing]:
    node = data
    for key in RESULTS_PATH:
        node = node.get(key) if isinstance(node, dict) else None
    listings = []
    for item in node if isinstance(node, list) else []:
        try:
            listings.append(_parse_item(item))
        except (KeyError, TypeError, ValueError):
            continue
    return listings


def parse_item_detail(data: dict) -> list[str]:
    raw = data.get(FIELD_DETAIL_IMAGES) if isinstance(data, dict) else None
    if not isinstance(raw, str):
        return []
    urls = [_image_url(part, data.get(FIELD_IMAGE_SERVER) or "")
            for part in raw.split(IMAGE_SEPARATOR)]
    return [url for url in urls if url][:MAX_PHOTOS]


class ShopGoodwillSource:
    name = "shopgoodwill"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def search(self, query: str) -> list[Listing]:
        resp = self._http.request("POST", SEARCH_URL, json=build_search_body(query))
        return parse_search(resp.json())

    def photos(self, source_id: str, known: list[str]) -> list[str]:
        url = ITEM_URL.format(item_id=quote(source_id, safe=""))
        try:
            resp = self._http.request("GET", url)
            data = resp.json()
        except SourceBlocked:
            raise  # a block stops the source; it is not a missing photo
        except (SourceError, ValueError):
            return known
        return parse_item_detail(data) or known
