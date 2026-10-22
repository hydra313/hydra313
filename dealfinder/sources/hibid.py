from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Callable

from dealfinder.models import Listing
from dealfinder.sources.base import HttpClient

# HiBid has no public docs. Everything below is an ASSUMPTION about its GraphQL
# schema; fix the URL, the query and the field names here.
GRAPHQL_URL = "https://hibid.com/graphql"
LOT_URL = "https://hibid.com/lot/{lot_id}"
PAGE_LENGTH = 100
MAX_PHOTOS = 8
LOT_SEARCH_QUERY = """
query LotSearch($searchText: String!, $pageNumber: Int!, $pageLength: Int!) {
  lotSearch(input: {searchText: $searchText}, pageNumber: $pageNumber, pageLength: $pageLength) {
    pagedResults {
      results {
        id
        lead
        quantity
        shippingOffered
        lotState { highBid minBid bidCount timeLeftSeconds isClosed }
        auction { buyerPremium shippingOffered }
        pictures { fullSizeLocation }
      }
    }
  }
}
""".strip()
RESULTS_PATH = ("data", "lotSearch", "pagedResults", "results")


def build_request(query: str, page: int = 1) -> dict:
    return {
        "query": LOT_SEARCH_QUERY,
        "variables": {"searchText": query, "pageNumber": page, "pageLength": PAGE_LENGTH},
    }


def _number(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _parse_lot(lot: dict, now: datetime) -> Listing | None:
    lot_id = lot["id"]
    title = lot["lead"]
    if lot_id in (None, "") or not isinstance(title, str) or not title.strip():
        raise ValueError("lot without id or title")
    state = lot["lotState"]
    if state.get("isClosed"):
        return None
    high_bid = _number(state.get("highBid") or 0)
    price = high_bid if high_bid > 0 else _number(state["minBid"])
    left = state.get("timeLeftSeconds")
    end_time = None if left is None else now + timedelta(seconds=_number(left))
    auction = lot.get("auction") or {}
    premium = auction.get("buyerPremium")
    pictures = lot.get("pictures") or []
    urls = [p["fullSizeLocation"] for p in pictures if p.get("fullSizeLocation")]
    source_id = str(lot_id)
    return Listing(
        source="hibid",
        source_id=source_id,
        title=title,
        url=LOT_URL.format(lot_id=source_id),
        price=price,
        shipping=None,
        shipping_offered=bool(lot.get("shippingOffered") or auction.get("shippingOffered")),
        image_urls=urls[:MAX_PHOTOS],
        is_auction=True,
        end_time=end_time,
        bid_count=int(state.get("bidCount") or 0),
        buyer_premium_pct=None if premium is None else _number(premium) / 100,
    )


def parse_search(data: dict, now: datetime) -> list[Listing]:
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    node = data
    for key in RESULTS_PATH:
        node = node.get(key) if isinstance(node, dict) else None
    listings = []
    for lot in node if isinstance(node, list) else []:
        try:
            listing = _parse_lot(lot, now)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
        if listing is not None:
            listings.append(listing)
    return listings


class HiBidSource:
    name = "hibid"

    def __init__(self, http: HttpClient, clock: Callable[[], datetime]) -> None:
        self._http = http
        self._clock = clock

    def search(self, query: str) -> list[Listing]:
        resp = self._http.request("POST", GRAPHQL_URL, json=build_request(query))
        return parse_search(resp.json(), self._clock())

    def photos(self, source_id: str, known: list[str]) -> list[str]:
        return known
