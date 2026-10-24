"""Sold-search snapshots read by the user's browser, with no API or session replay."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from dealfinder import db
from dealfinder.models import Comp
from dealfinder.sources.ebay_browser import ITEM_ID, MAX_PAGE_BYTES
from dealfinder.sources.ebay_sold import SoldFetcher, parse_sold

SNAPSHOTS = "ebay_browser_sold_snapshots"
REVISION = "ebay_browser_sold_revision"
MAX_QUERIES = 100


def snapshots(conn) -> dict:
    try:
        data = json.loads(db.get_setting(conn, SNAPSHOTS, "{}"))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def save_sold_capture(conn, query: str, html: str, captured_at: datetime) -> int:
    if not query.strip() or len(query) > 200 or len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValueError("Invalid sold search or oversized capture")
    # A login page must never replace usable sold evidence with an empty success.
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(html)
    if not tree.css("li.s-card,li.s-item") and "0 results" not in tree.text().lower():
        raise ValueError("No sold results. Sign in or complete verification in the helper tab.")
    found = {}
    for comp in parse_sold(html):
        parts = urlsplit(comp.url)
        identity = ITEM_ID.search(parts.path)
        if (not comp.sold_date or not 0 <= (captured_at.date() - comp.sold_date).days <= 90
                or comp.price <= 0 or parts.scheme != "https"
                or parts.hostname not in ("www.ebay.com", "ebay.com") or not identity):
            continue
        comp.url = "https://www.ebay.com/itm/" + identity[1]
        found[comp.url] = {**asdict(comp), "sold_date": comp.sold_date.isoformat()}
    data = snapshots(conn)
    data[query] = {"at": captured_at.isoformat(), "comps": list(found.values())[:240]}
    data = dict(sorted(data.items(), key=lambda pair: pair[1]["at"])[-MAX_QUERIES:])
    db.set_setting(conn, SNAPSHOTS, json.dumps(data))
    revision = int(db.get_setting(conn, REVISION, "0")) + 1
    db.set_setting(conn, REVISION, str(revision))
    return len(found)


class BrowserSoldFetcher(SoldFetcher):
    def __init__(self, http, conn, cfg, clock):
        super().__init__(http)
        self.conn, self.cfg, self.clock = conn, cfg, clock
        self._revision = None
        self._snapshots = {}

    def _snapshot(self, query):
        revision = db.get_setting(self.conn, REVISION, "0")
        if revision != self._revision:
            self._snapshots = snapshots(self.conn)
            self._revision = revision
        item = self._snapshots.get(query)
        if item and self.clock() - datetime.fromisoformat(item["at"]) < timedelta(hours=self.cfg.market_ttl_hours):
            return item
        return None

    def captured_after(self, query, at):
        item = self._snapshot(query)
        return bool(item and datetime.fromisoformat(item["at"]) > at)

    def has_capture(self, query):
        return self._snapshot(query) is not None

    def fetch(self, query):
        item = self._snapshot(query)
        if item is None:
            return super().fetch(query)
        return [Comp(**{**comp, "sold_date": date.fromisoformat(comp["sold_date"])})
                for comp in item["comps"]]


def needed_queries(conn, catalog, cfg, now) -> list[str]:
    """Request sold evidence only for live catalog matches actually on the dashboard."""
    products = {p.id: p for p in catalog.enabled_products()}
    bulk = {b.id: b for b in catalog.enabled_bulk()}
    platforms = {p.id: p for p in catalog.enabled_platforms()}
    captured = snapshots(conn)
    queries = []
    cutoff = (now - timedelta(hours=2 * cfg.ebay_full_pass_hours)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT kind, key, condition, category FROM listings WHERE dismissed = 0"
        " AND julianday(last_seen) >= julianday(?)"
        " AND (end_time IS NULL OR julianday(end_time) > julianday(?))",
        (cutoff, now.isoformat())).fetchall()
    for row in rows:
        if row["kind"] == "single" and row["key"] in products:
            query = products[row["key"]].ebay_query
        elif row["category"] is None and row["key"] in platforms:
            query = platforms[row["key"]].ebay_lot_query
        elif row["key"] in bulk:
            query = bulk[row["key"]].ebay_query
        else:
            continue
        item = captured.get(query)
        if item and now - datetime.fromisoformat(item["at"]) < timedelta(hours=cfg.market_ttl_hours):
            continue
        if query not in queries:
            queries.append(query)
    return queries
