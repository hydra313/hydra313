"""Import rendered HiBid search cards, without replaying browser sessions or APIs."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote_plus, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from dealfinder import db
from dealfinder.config import Config
from dealfinder.models import Listing
from dealfinder.sources.base import SourceNeedsAttention

BASE_URL = "https://hibid.com"
SEARCH_URL = BASE_URL + "/lots?q={query}&miles=-1"
SNAPSHOT_KEY = "hibid_browser_snapshots"
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_CARDS = 1000
MAX_QUERIES = 30
IMPORT_LOCK = threading.Lock()
SELECTORS = {
    "card": ".app-auction-lot-card",
    "title": ".app-auction-lot-card__title",
    "money": ".app-auction-lot-card__meta-money",
    "end": ".app-auction-lot-badge__value",
    "shipping": ".app-auction-lot-card__shipping",
    "bid": ".app-auction-lot-card__cta",
    "image": ".app-auction-lot-card__image",
}
USD = re.compile(r"\$\s*([\d,]+\.\d{2})\s+USD\b")
DURATION = re.compile(r"(?:(\d{1,3})d\s*)?(?:(\d{1,3})h\s*)?"
                      r"(?:(\d{1,3})m\s*)?(?:(\d{1,3})s\s*)?", re.I)
LOT_PATH = re.compile(r"^/lot/(\d+)(?:/|$)")
BIDS = re.compile(r"\b(\d[\d,]*)\s+bids?\b", re.I)


@dataclass
class BrowserPage:
    cards: int = 0
    listings: list[Listing] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


def _text(node) -> str:
    return " ".join(node.text(separator=" ").split()) if node is not None else ""


def _host(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return (parts.scheme == "https" and (host == "hibid.com" or host.endswith(".hibid.com"))
            and parts.username is None and parts.password is None
            and parts.port in (None, 443))


def _price(text: str) -> float | None:
    m = USD.search(text)
    if not m:
        return None
    value = float(m[1].replace(",", ""))
    return value if math.isfinite(value) and value >= 0 else None


def _duration(text: str) -> timedelta | None:
    match = DURATION.fullmatch(text.strip())
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(v or 0) for v in match.groups())
    delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return delta if delta.total_seconds() > 0 else None


def _listing(card, captured_at: datetime, *, fragment: bool = False) -> tuple[Listing | None, str]:
    title_node = (card.css_first('a[href*="/lot/"]') if fragment
                  else card.css_first(SELECTORS["title"]))
    if title_node is None:
        return None, "missing title or link"
    url = urljoin(BASE_URL, title_node.attributes.get("href") or "")
    path = LOT_PATH.match(urlsplit(url).path)
    title = _text(title_node)
    if not _host(url) or not path or not title or len(title) > 1000:
        return None, "invalid listing link"
    full_text = _text(card)
    shipping = full_text if fragment else _text(card.css_first(SELECTORS["shipping"]))
    if "shipping available" not in shipping.lower() or "pickup only" in shipping.lower():
        return None, "pickup only or shipping unknown"
    money = full_text if fragment else _text(card.css_first(SELECTORS["money"]))
    price = _price(money)
    if price is None:
        return None, "missing USD price (other currencies are excluded)"
    bids = BIDS.search(money)
    bid_count = int(bids[1].replace(",", "")) if bids else 0
    if price == 0:
        bid_text = full_text if fragment else _text(card.css_first(SELECTORS["bid"]))
        # The bidding button is the minimum payable amount, not the $0 current bid.
        next_bid = re.search(r"\bbid\s+([\d,]+\.\d{2})\s+USD\b", bid_text, re.I)
        price = float(next_bid[1].replace(",", "")) if next_bid else 0
    if not math.isfinite(price) or price <= 0:
        return None, "no payable bid amount"
    if fragment:
        # Clipboard HTML may lose the card CSS. Read a standalone countdown element.
        duration = next((_duration(_text(n)) for n in card.css("p,span,div")
                         if _duration(_text(n)) is not None), None)
    else:
        duration = _duration(_text(card.css_first(SELECTORS["end"])))
    if duration is None:
        return None, "missing closing countdown (closed, upcoming or webcast)"
    images = []
    for img in card.css("img" if fragment else SELECTORS["image"]):
        image_url = urljoin(BASE_URL, img.attributes.get("src") or "")
        if _host(image_url) and urlsplit(image_url).hostname == "cdn.hibid.com":
            images.append(image_url)
    return Listing(
        source="hibid", source_id=path[1], title=title,
        url=f"{BASE_URL}/lot/{path[1]}", price=price, shipping=None,
        shipping_offered=True, image_urls=list(dict.fromkeys(images))[:8], is_auction=True,
        end_time=captured_at + duration, bid_count=bid_count, observed_at=captured_at,
    ), ""


def parse_browser_page(html: str, captured_at: datetime) -> BrowserPage:
    if len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValueError("HiBid page is too large (maximum 5 MB).")
    if captured_at.tzinfo is None:
        raise ValueError("HiBid capture time must include a timezone.")
    tree = LexborHTMLParser(html)
    # Never inspect or store scripts, cookies, session state or hidden JSON.
    for node in tree.css("script,style,noscript,svg,template"):
        node.decompose()
    cards = tree.css(SELECTORS["card"])
    fragment = False
    if not cards:
        # Copying rendered page content sometimes drops the outer card wrapper.
        # Split groups at each lot link; ignore duplicate links for the same lot.
        anchors = tree.css('a[href*="/lot/"]')
        if anchors:
            card_html: list[list[str]] = []
            previous = None
            for child in tree.body.iter() if tree.body else []:
                if child.tag == "-text":
                    continue
                link = child if child.tag == "a" else child.css_first('a[href*="/lot/"]')
                if link is not None:
                    href = link.attributes.get("href") or ""
                    if href != previous:
                        card_html.append([])
                        previous = href
                if card_html:
                    card_html[-1].append(child.html)
            cards = [LexborHTMLParser("".join(group)).body for group in card_html]
            fragment = True
    if not cards:
        raise ValueError("No rendered HiBid results found. Open HiBid in your browser, wait "
                         "for lots to load, then copy the results or save the complete HTML page.")
    if len(cards) > MAX_CARDS:
        raise ValueError("Too many HiBid results; import at most 1000 cards at a time.")
    listings: dict[str, Listing] = {}
    skipped: Counter = Counter()
    for card in cards:
        try:
            listing, reason = _listing(card, captured_at, fragment=fragment)
        except (ValueError, TypeError, OverflowError):
            listing, reason = None, "malformed listing"
        if listing is not None:
            listings[listing.source_id] = listing
        else:
            skipped[reason] += 1
    return BrowserPage(cards=len(cards), listings=list(listings.values()), skipped=dict(skipped))


def _snapshots(conn: sqlite3.Connection) -> dict:
    try:
        data = json.loads(db.get_setting(conn, SNAPSHOT_KEY, "{}"))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def save_snapshot(conn: sqlite3.Connection, query: str, page: BrowserPage,
                  captured_at: datetime, matched: int) -> None:
    query = query.strip()[:120] or "browser search"
    snapshots = _snapshots(conn)
    items = []
    for listing in page.listings:
        item = asdict(listing)
        item["end_time"] = listing.end_time.isoformat() if listing.end_time else None
        item["observed_at"] = captured_at.isoformat()
        items.append(item)
    snapshots[query] = {"captured_at": captured_at.isoformat(), "cards": page.cards,
                        "matched": matched, "skipped": page.skipped, "listings": items}
    snapshots = dict(sorted(snapshots.items(), key=lambda p: p[1]["captured_at"])[-MAX_QUERIES:])
    db.set_setting(conn, SNAPSHOT_KEY, json.dumps(snapshots))


def snapshot_status(conn: sqlite3.Connection, cfg: Config, now: datetime) -> dict:
    snapshots = _snapshots(conn)
    latest = max(snapshots.values(), key=lambda s: s["captured_at"], default=None)
    if latest is None:
        return {"state": "missing", "queries": 0, "count": 0, "captured_at": None}
    captured_at = datetime.fromisoformat(latest["captured_at"])
    expires = captured_at + timedelta(minutes=cfg.hibid_snapshot_minutes)
    return {"state": "ready" if now < expires else "expired", "queries": len(snapshots),
            "count": len(latest["listings"]), "captured_at": captured_at, "expires": expires,
            "cards": latest["cards"], "matched": latest["matched"], "skipped": latest["skipped"]}


class HiBidBrowserSource:
    name = "hibid"
    browser_assisted = True

    def __init__(self, conn: sqlite3.Connection, cfg: Config,
                 clock: Callable[[], datetime]) -> None:
        self.conn, self.cfg, self.clock = conn, cfg, clock

    def search(self, query: str) -> list[Listing]:
        now = self.clock()
        snapshots = _snapshots(self.conn)
        latest: dict[str, Listing] = {}
        for snapshot in sorted(snapshots.values(), key=lambda s: s["captured_at"]):
            at = datetime.fromisoformat(snapshot["captured_at"])
            if now - at >= timedelta(minutes=self.cfg.hibid_snapshot_minutes):
                continue
            for item in snapshot["listings"]:
                listing = Listing(**{**item, "observed_at": at,
                                     "end_time": datetime.fromisoformat(item["end_time"])
                                     if item["end_time"] else None})
                latest[listing.source_id] = listing
        if not latest:
            reason = "snapshot expired or no usable USD lots" if snapshots else "browser import needed"
            raise SourceNeedsAttention(f"HiBid {reason}: import a fresh browser search page.")
        return list(latest.values())

    def photos(self, source_id: str, known: list[str]) -> list[str]:
        return known


def search_link(query: str) -> str:
    return SEARCH_URL.format(query=quote_plus(query))
