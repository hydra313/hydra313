"""Keyless eBay ingestion from rendered, fixed-price USD search cards."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlsplit

from selectolax.lexbor import LexborHTMLParser

from dealfinder import db
from dealfinder.config import Config
from dealfinder.models import Listing
from dealfinder.sources.base import SourceNeedsAttention
from dealfinder.sources.hibid_browser import BrowserPage

MAX_PAGE_BYTES = 2 * 1024 * 1024
STATUS_KEY = "ebay_browser_status"
ROTATION_KEY = "ebay_browser_rotation"
SELECTORS = ("li.s-card", "li.s-item")
ITEM_ID = re.compile(r"/itm/(?:[^/]+/)?(\d{9,15})(?:/|$)")
USD = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")


def search_link(query: str, auction: bool = False, page: int = 1) -> str:
    return ("https://www.ebay.com/sch/i.html?_nkw=" + quote_plus(query)
            + ("&_sop=1&LH_Auction=1" if auction else "&_sop=10")
            + "&_ipg=240&_pgn=" + str(max(1, page)))


def _text(node) -> str:
    return " ".join(node.text(separator=" ").split()) if node else ""


def _listing(card, captured_at: datetime) -> Listing | None:
    title = _text(card.css_first(".s-card__title, .s-item__title"))
    title = re.sub(r"^new listing\s*", "", title, flags=re.I)
    title = re.sub(r"\s*Opens in a new window or tab\s*$", "", title, flags=re.I)
    if not title or title.lower() == "shop on ebay" or len(title) > 1000:
        return None
    text = _text(card)
    bids = re.search(r"\b([\d,]+)\s+bids?\b", text, re.I)
    end_time = None
    if bids:
        countdown = re.search(r'((?:\d+\s*[dhms]\s*)+)\s*left\b', text, re.I)
        if countdown:
            seconds = sum(int(n) * {'d':86400,'h':3600,'m':60,'s':1}[u.lower()]
                          for n,u in re.findall(r'(\d+)\s*([dhms])', countdown[1], re.I))
            if 0 < seconds <= 35 * 86400:
                end_time = captured_at + timedelta(seconds=seconds)
        if end_time is None:
            return None  # never invent auction closing times
    if ((not bids and not re.search(r"\bbuy it now\b|\bor best offer\b", text, re.I))
            or re.search(r"\b(sold|ended|pickup only)\b", text, re.I)):
        return None
    raw_price = _text(card.css_first(".s-card__price, .s-item__price"))
    money = USD.fullmatch(raw_price)
    if not money:
        return None
    price = float(money[1].replace(",", ""))
    if not 0 < price < 100_000_000:
        return None
    link = card.css_first('a[href*="/itm/"]')
    parts = urlsplit(link.attributes.get("href", "") if link else "")
    match = ITEM_ID.search(parts.path)
    if (parts.scheme != "https" or parts.hostname not in ("www.ebay.com", "ebay.com")
            or parts.username or parts.password or parts.port not in (None, 443) or not match):
        return None
    shipping = None
    for row in card.css(".s-card__attribute-row, .s-item__shipping, .s-item__logisticsCost"):
        line = _text(row)
        if not re.search(r"\b(shipping|delivery)\b", line, re.I):
            continue
        if re.search(r"\bfree\b", line, re.I):
            shipping = 0.0
        else:
            amount = re.fullmatch(r"\+?\s*\$([\d,]+(?:\.\d{1,2})?)\s+(?:delivery|shipping)", line, re.I)
            if amount:
                shipping = float(amount[1].replace(",", ""))
        break
    conditions = " ".join(_text(n) for n in card.css(".s-card__subtitle, .SECONDARY_INFO"))
    if re.search(r"\b(parts|not working)\b", conditions, re.I):
        # Preserve parts lots, but ensure the single-item and game-lot guards see this.
        title += " (for parts)"
        condition_id = "7000"
    else:
        condition_id = ("1000" if re.search(r"\bbrand new\b", conditions, re.I) else
                        "1500" if re.search(r"\bopen box\b|\bnew \(other\)", conditions, re.I) else "3000")
    images = []
    for image in card.css("img"):
        url = image.attributes.get("src", "")
        image_parts = urlsplit(url)
        if image_parts.scheme == "https" and image_parts.hostname == "i.ebayimg.com":
            images.append(url)
    return Listing("ebay", f"v1|{match[1]}|0", title,
                   f"https://www.ebay.com/itm/{match[1]}", price, shipping, True,
                   list(dict.fromkeys(images))[:8], bool(bids), end_time,
                   bid_count=int(bids[1].replace(',', '')) if bids else 0,
                   condition_id=condition_id, observed_at=captured_at)


def parse_active_page(html: str, captured_at: datetime) -> BrowserPage:
    if len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValueError("eBay import exceeds 2 MB.")
    if captured_at.tzinfo is None:
        raise ValueError("eBay capture time needs a timezone.")
    tree = LexborHTMLParser(html)
    for node in tree.css("script,style,noscript,svg,template"):
        node.decompose()
    cards = tree.css(",".join(SELECTORS))
    if len(cards) > 250:
        raise ValueError("Import at most 250 eBay cards at a time.")
    if not cards and not re.search(r"\b0 results\b|no exact matches found", _text(tree.body), re.I):
        raise ValueError("No eBay results found. Complete browser verification or reload the search.")
    found = {}
    for card in cards:
        try:
            item = _listing(card, captured_at)
        except (ValueError, TypeError, OverflowError):
            continue
        if item:
            found[item.source_id] = item
    return BrowserPage(len(cards), list(found.values()), {})


def browser_status(conn: sqlite3.Connection) -> dict:
    try:
        result = json.loads(db.get_setting(conn, STATUS_KEY, "{}"))
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


class EbayBrowserSource:
    name = "ebay"
    browser_assisted = True

    def __init__(self, conn: sqlite3.Connection, cfg: Config, clock) -> None:
        self.conn, self.cfg, self.clock = conn, cfg, clock

    def search(self, query: str) -> list[Listing]:
        cutoff = self.clock() - timedelta(hours=2 * self.cfg.ebay_full_pass_hours)
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE source = 'ebay' AND dismissed = 0"
            " AND NOT (kind = 'game_lot' OR (kind = 'scan_only' AND category IS NULL))"
            " AND julianday(last_seen) >= julianday(?)", (cutoff.isoformat(),)).fetchall()
        if not rows:
            raise SourceNeedsAttention("Connect the eBay browser helper to collect keyless listings.")
        return [Listing(
            source="ebay", source_id=r["source_id"], title=r["title"], url=r["url"],
            price=r["price"], shipping=r["shipping"], shipping_offered=bool(r["shipping_offered"]),
            image_urls=json.loads(r["image_urls"] or "[]"), is_auction=bool(r["is_auction"]),
            end_time=datetime.fromisoformat(r["end_time"]) if r["end_time"] else None,
            bid_count=r["bid_count"], observed_at=datetime.fromisoformat(r["last_seen"]),
            condition_id="1000" if r["condition"] == "new" else "3000",
        ) for r in rows if not r["end_time"] or datetime.fromisoformat(r["end_time"]) > self.clock()]

    def photos(self, source_id: str, known: list[str]) -> list[str]:
        return known
