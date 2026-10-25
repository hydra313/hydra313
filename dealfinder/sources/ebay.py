from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote

from dealfinder.models import Listing
from dealfinder.sources.base import HttpClient, SourceError

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/{item_id}"
SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE_ID = "EBAY_US"
MAX_PHOTOS = 8
TOKEN_MARGIN_S = 60


def _parse_end_time(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _money(obj: dict | None) -> float | None:
    if not obj or obj.get("value") is None:
        return None
    return float(obj["value"])


def _shipping(item: dict) -> float | None:
    options = item.get("shippingOptions") or []
    if options and options[0].get("shippingCostType") == "FIXED":
        return _money(options[0].get("shippingCost"))
    return None


def _image_urls(item: dict) -> list[str]:
    urls = []
    if (item.get("image") or {}).get("imageUrl"):
        urls.append(item["image"]["imageUrl"])
    for extra in item.get("additionalImages") or []:
        if extra.get("imageUrl"):
            urls.append(extra["imageUrl"])
    return urls


def _parse_item(item: dict) -> Listing | None:
    options = item.get("buyingOptions") or []
    is_auction = "AUCTION" in options
    price = None
    if is_auction:
        price = _money(item.get("currentBidPrice"))
    if price is None:
        price = _money(item.get("price"))
    if price is None:
        return None
    return Listing(
        source="ebay",
        source_id=item["itemId"],
        title=item.get("title", ""),
        url=item.get("itemWebUrl", ""),
        price=price,
        shipping=_shipping(item),
        shipping_offered=True,
        image_urls=_image_urls(item),
        is_auction=is_auction,
        end_time=_parse_end_time(item.get("itemEndDate")),
        bid_count=item.get("bidCount") or 0,
        condition_id=item.get("conditionId"),
    )


def parse_search(data: dict) -> list[Listing]:
    listings = []
    for item in data.get("itemSummaries") or []:
        try:  # one malformed item never costs the rest of the page
            listing = _parse_item(item)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
        if listing is not None:
            listings.append(listing)
    return listings


def _json(resp) -> dict:
    try:
        return resp.json()
    except ValueError as e:
        raise SourceError(f"eBay returned invalid JSON: {e}") from e


class EbaySource:
    name = "ebay"

    def __init__(
        self,
        http: HttpClient,
        client_id: str,
        client_secret: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._token: str | None = None
        self._token_expires: datetime | None = None

    def _auth_headers(self) -> dict[str, str]:
        now = self._clock()
        if self._token is None or self._token_expires is None or now >= self._token_expires:
            resp = self._http.request(
                "POST",
                TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials", "scope": SCOPE},
            )
            body = resp.json()
            self._token = body["access_token"]
            self._token_expires = now + timedelta(seconds=body["expires_in"] - TOKEN_MARGIN_S)
        return {
            "Authorization": f"Bearer {self._token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
        }

    def search(self, query: str, fixed_price_only: bool = False) -> list[Listing]:
        buying = "FIXED_PRICE" if fixed_price_only else "AUCTION|FIXED_PRICE"
        params = {
            "q": query,
            "limit": "200",
            "filter": (
                f"buyingOptions:{{{buying}}},deliveryOptions:{{SHIP_TO_HOME}},"
                "itemLocationCountry:US"
            ),
        }
        resp = self._http.request(
            "GET", SEARCH_URL, params=params, headers=self._auth_headers()
        )
        return parse_search(_json(resp))

    def photos(self, source_id: str, known: list[str]) -> list[str]:
        url = ITEM_URL.format(item_id=quote(source_id, safe=""))
        resp = self._http.request("GET", url, headers=self._auth_headers())
        return _image_urls(_json(resp))[:MAX_PHOTOS]
