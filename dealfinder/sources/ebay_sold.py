from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser, LexborNode as Node

from dealfinder.models import Comp
from dealfinder.sources.base import HttpClient

SOLD_SEARCH_URL = "https://www.ebay.com/sch/i.html"
SOLD_PARAMS = "LH_Sold=1&LH_Complete=1&LH_PrefLoc=1&_ipg=240"

# Every selector lives here so a live layout change is a one-place edit.
# "legacy" is the older li.s-item markup, "card" the current li.s-card one.
# Lists are tried in order or all at once, depending on the field (see parse).
SELECTORS = {
    "legacy": {
        "item": "li.s-item",
        "title": ".s-item__title",
        "price": ".s-item__price",
        "shipping": [".s-item__shipping", ".s-item__logisticsCost"],
        "caption": [".s-item__caption--signal", ".POSITIVE"],
        "condition": ".SECONDARY_INFO",
        "link": "a.s-item__link",
    },
    "card": {
        "item": "li.s-card",
        "title": ".s-card__title",
        "price": ".s-card__price",
        # attribute rows whose text contains one of these words hold the shipping cost
        "shipping": [".s-card__attribute-row"],
        "shipping_words": ("delivery", "shipping"),
        "caption": [".s-card__caption"],
        "condition": ".s-card__subtitle",
        "link": "a.su-link",
    },
}

PLACEHOLDER_TITLE = "shop on ebay"
TITLE_PREFIX = re.compile(r"^\s*New Listing\s*", re.IGNORECASE)
TITLE_SUFFIX = re.compile(r"\s*Opens in a new window or tab\s*$", re.IGNORECASE)
PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
SOLD_RE = re.compile(r"Sold\s+([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2}),?\s+(\d{4})")
MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}


def sold_url(query: str) -> str:
    return f"{SOLD_SEARCH_URL}?_nkw={quote_plus(query)}&{SOLD_PARAMS}"


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip()


def _clean_title(raw: str) -> str:
    title = TITLE_SUFFIX.sub("", TITLE_PREFIX.sub("", raw))
    return title.strip()


def _parse_price(text: str) -> float | None:
    # A price range ("$20.00 to $30.00") or a foreign-currency price is not a usable comp.
    m = PRICE_RE.fullmatch(text.strip())
    if m is None:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_shipping(item: Node, sel: dict) -> float:
    words = sel.get("shipping_words")
    for css in sel["shipping"]:
        for node in item.css(css):
            text = _text(node)
            if words and not any(w in text.lower() for w in words):
                continue
            if "free" in text.lower():
                return 0.0
            m = PRICE_RE.search(text)
            if m:
                return float(m.group(1).replace(",", ""))
    return 0.0


def _parse_sold_date(item: Node, sel: dict) -> date | None:
    for css in sel["caption"]:
        for node in item.css(css):
            m = SOLD_RE.search(_text(node))
            if m is None:
                continue
            month = MONTHS.get(m.group(1).lower())
            if month is None:
                continue
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                continue
    return None


def _parse_item(item: Node, sel: dict) -> Comp | None:
    title = _clean_title(_text(item.css_first(sel["title"])))
    if not title or title.lower() == PLACEHOLDER_TITLE:
        return None
    price = _parse_price(_text(item.css_first(sel["price"])))
    if price is None:
        return None
    link = item.css_first(sel["link"])
    url = (link.attributes.get("href") or "") if link is not None else ""
    condition = _text(item.css_first(sel["condition"])) or None
    return Comp(
        title=title,
        price=round(price + _parse_shipping(item, sel), 2),
        sold_date=_parse_sold_date(item, sel),
        url=url,
        condition_label=condition,
    )


def parse_sold(html: str) -> list[Comp]:
    tree = LexborHTMLParser(html)
    comps: list[Comp] = []
    for sel in SELECTORS.values():
        for item in tree.css(sel["item"]):
            try:
                comp = _parse_item(item, sel)
            except Exception:  # one malformed item must never discard the page
                continue
            if comp is not None:
                comps.append(comp)
    return comps


class SoldFetcher:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def fetch(self, query: str) -> list[Comp]:
        resp = self._http.request("GET", sold_url(query))
        return parse_sold(resp.text)
