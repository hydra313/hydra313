"""Dashboard routes: Deals, lot pages, listing detail, catalog, header controls (§14)."""
from __future__ import annotations

import json
import io
import logging
import math
import re
import sqlite3
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from dealfinder import db
from dealfinder.budget import Budget
from dealfinder.catalog import Catalog, load_catalog
from dealfinder.config import Config
from dealfinder.console_editions import edition_text
from dealfinder.deal_rating import compare_deal
from dealfinder.models import BulkProduct, MarketValue
from dealfinder.market import SOLD_ERROR_SETTING
from dealfinder.pipeline import SCAN_KINDS, stale_before, hibid_snapshot_before
from dealfinder.pipeline import ebay_queries
from dealfinder.sources import ebay_browser
from dealfinder.sources import ebay_sold_browser
from dealfinder.sources.ebay_sold import sold_url
from dealfinder.sources.hibid_browser import (
    IMPORT_LOCK, MAX_PAGE_BYTES, parse_browser_page, save_snapshot, search_link, snapshot_status,
)
from dealfinder.web.app import local_now, utc_now

log = logging.getLogger(__name__)

router = APIRouter()

SITE_NAMES = {"ebay": "eBay", "shopgoodwill": "ShopGoodwill", "hibid": "HiBid"}
CATEGORIES = ("gpu", "cpu", "console", "controller")
LAST_VISIT = "last_visit"
NEW_SINCE = "new_since"  # the visit before the current page, for its HTMX filter requests
_ERROR_MAX = 200


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return request.app.state.templates.TemplateResponse(
        request, name, context, status_code=status_code)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _ago(when: datetime, now: datetime) -> str:
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def _time_left(end_time: str | None, now: datetime) -> str:
    end = _parse_time(end_time)
    if end is None:
        return "—"
    minutes = max(0, int((end - now).total_seconds() // 60))
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _http_url(url: str | None) -> str | None:
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else None


def _is_partial(request: Request) -> bool:
    """An HTMX filter request, which gets only the rows."""
    return (request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-History-Restore-Request") != "true")


def _catalog(conn: sqlite3.Connection, cfg: Config) -> Catalog | None:
    try:
        return load_catalog(cfg.catalog_dir, conn, cfg)
    except Exception:  # noqa: BLE001 - pages still render without catalog names
        log.exception("loading the catalog failed")
        return None


# --- header --------------------------------------------------------------------


def _last_success(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = conn.execute(
        "SELECT source, finished_at FROM runs WHERE id IN"
        " (SELECT MAX(id) FROM runs WHERE status = 'ok' GROUP BY source)"
    ).fetchall()
    finished = {r["source"]: _parse_time(r["finished_at"]) for r in rows}
    out = []
    for source, name in SITE_NAMES.items():
        at = finished.get(source)
        out.append({
            "name": name,
            "ago": _ago(at, now) if at else "never",
            "at": at.astimezone().strftime("%Y-%m-%d %H:%M") if at else "",
        })
    return out


def _banners(conn: sqlite3.Connection, cfg: Config, now: datetime,
             scan_error: tuple[int, str] | None) -> list[dict]:
    """Each banner has a level, a text and optionally the listing ids it concerns."""
    banners = []
    sold_error = db.get_setting(conn, SOLD_ERROR_SETTING)
    if sold_error:
        banners.append({"level": "warn", "text": "eBay sold-price lookup failed: "
                        + sold_error[:_ERROR_MAX] + ". Market values may be unavailable"
                        " or cached. Unpriced items remain visible in Show all listings mode."})
    for run in db.latest_runs(conn):
        if run["source"] == "hibid" and cfg.hibid_mode != "api":
            continue  # browser mode reports its snapshot state, not a historical API error
        site = SITE_NAMES.get(run["source"], run["source"])
        if run["status"] == "error":
            error = (run["error"] or "unknown error")[:_ERROR_MAX]
            banners.append({"level": "warn", "text": f"{site}: last run failed ({error})"})
        elif run["status"] == "needs_attention":
            banners.append({"level": "warn", "text": f"{site}: {run['error'] or 'needs attention'}"[:_ERROR_MAX],
                            "link": "/sources/ebay" if run["source"] == "ebay" else None,
                            "link_text": "Connect browser helper"})
        elif run["status"] == "site_change":
            banners.append({"level": "warn",
                            "text": f"{site}: possible site change. The last run found"
                                    " no listings"})
    if cfg.hibid_mode == "browser":
        snapshot = snapshot_status(conn, cfg, now)
        state = snapshot["state"]
        text = (f"HiBid browser snapshot: {snapshot['count']} usable lots. Prices expire after "
                f"{cfg.hibid_snapshot_minutes} minutes; refresh the browser page and import again."
                if state == "ready" else "HiBid needs your attention: "
                + ("browser snapshot expired. Import fresh results."
                   if state == "expired" else "browser import needed. Open a HiBid search first."))
        banners.append({"level": "info" if state == "ready" else "warn", "text": text,
                        "link": "/sources/hibid", "link_text": "HiBid browser import"})
    failures = db.notification_failures_since(conn, now - timedelta(hours=24))
    if failures:
        noun = "notification" if failures == 1 else "notifications"
        banners.append({"level": "warn",
                        "text": f"{failures} Discord {noun} failed in the last 24 hours"})
    scan_failures = db.scan_failures_since(conn, now - timedelta(hours=24))
    if scan_failures:
        n = len(scan_failures)
        noun = "photo scan" if n == 1 else "photo scans"
        banners.append({"level": "warn", "text": f"{n} {noun} failed in the last 24 h",
                        "listings": list(dict.fromkeys(scan_failures))})
    if scan_error is not None:
        listing_id, error = scan_error
        banners.append({"level": "warn", "text": error[:_ERROR_MAX], "listings": [listing_id]})
    if not (cfg.ebay_client_id and cfg.ebay_client_secret):
        banners.append({"level": "info",
                        "text": "eBay keyless mode uses your browser helper for Buy It Now listings.",
                        "link": "/sources/ebay", "link_text": "eBay browser setup"})
        helper = ebay_browser.browser_status(conn)
        if helper.get("error"):
            banners.append({"level": "warn", "text": "eBay browser helper: " + str(helper["error"])[:_ERROR_MAX],
                            "link": "/sources/ebay", "link_text": "View helper status"})
    if not cfg.anthropic_api_key:
        banners.append({"level": "info",
                        "text": "ANTHROPIC_API_KEY not set: photo scanning is unavailable."})
    if not cfg.discord_webhook_url:
        banners.append({"level": "info",
                        "text": "DISCORD_WEBHOOK_URL not set: Discord notifications are off."})
    return banners


def _header(request: Request, conn: sqlite3.Connection, caps_error: str | None = None,
            refresh_status: str | None = None) -> dict:
    cfg = request.app.state.cfg
    refreshing = request.app.state.runner.refresh_running
    budget = Budget(conn, cfg, local_now)
    now = utc_now()
    runner = request.app.state.runner
    progress = runner.progress
    if refreshing:
        site = SITE_NAMES.get(progress.get("source"), "sources")
        detail = (f" {progress.get('query_number', 0)}/{progress.get('query_count', 0)}"
                  if progress.get("query_count") else "")
        status = (f"Refreshing… {progress.get('stage', 'Starting')} {site}{detail} · "
                  f"{progress.get('new', 0)} new · {progress.get('updated', 0)} updated")
    elif runner.refresh_error:
        status = "Refresh failed: " + runner.refresh_error
    elif runner.last_report is not None:
        report = runner.last_report
        failed = sum(s != "ok" for s, _ in report.per_source.values())
        status = (f"Refresh complete · {report.new} new · {report.updated} updated · "
                  f"{report.matched} catalog matches" + (f" · {failed} sources need attention" if failed else ""))
    else:
        status = "Ready to check for listings"
    scheduler = getattr(request.app.state, "scheduler", None)
    jobs = scheduler.get_jobs() if scheduler else []
    next_run = jobs[0].next_run_time if jobs else None
    keyless = not (cfg.ebay_client_id and cfg.ebay_client_secret)
    helper = ebay_browser.browser_status(conn) if keyless else {}
    helper_at = _parse_time(helper.get("connected_at"))
    return {
        "refreshing": refreshing,
        "refresh_status": refresh_status or status,
        "refresh_minutes": cfg.refresh_minutes,
        "next_check": f"{max(1, math.ceil((next_run - now).total_seconds() / 60))} min" if next_run else None,
        "progress_query": progress.get("query", "") if refreshing else "",
        "ebay_helper_status": ("eBay helper: " + ("needs attention" if helper.get("error") else
                               "connected" if helper_at and now - helper_at < timedelta(minutes=2) else
                               "not connected; cached results only")) if keyless else "",
        "scan_enabled": budget.scan_enabled(),
        "paused": budget.paused(),
        "spent_today": budget.spent_today(),
        "spent_month": budget.spent_this_month(),
        "caps": budget.caps(),
        "caps_error": caps_error,
        "last_success": _last_success(conn, now),
        "banners": _banners(conn, cfg, now, request.app.state.runner.scan_error),
    }


def _header_response(request: Request, conn: sqlite3.Connection,
                     caps_error: str | None = None, status_code: int = 200):
    ctx = _header(request, conn, caps_error)
    return _render(request, "partials/header_controls.html", ctx, status_code)


def _status_response(request: Request, conn: sqlite3.Connection,
                     refresh_status: str | None = None):
    ctx = _header(request, conn, refresh_status=refresh_status)
    response = _render(request, "partials/header_status.html", ctx)
    response.headers["HX-Trigger"] = "listings-updated"
    return response


@router.get("/header/status", response_class=HTMLResponse)
def header_status(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Continuously poll status and update rows, including scheduler-started runs."""
    return _status_response(request, conn)


@router.post("/controls/scan", response_class=HTMLResponse)
def toggle_scan(request: Request, on: bool = Form(...),
                conn: sqlite3.Connection = Depends(get_conn)):
    Budget(conn, request.app.state.cfg, local_now).set_scan_enabled(on)
    return _header_response(request, conn)


@router.post("/controls/pause", response_class=HTMLResponse)
def toggle_pause(request: Request, on: bool = Form(...),
                 conn: sqlite3.Connection = Depends(get_conn)):
    Budget(conn, request.app.state.cfg, local_now).set_paused(on)
    return _header_response(request, conn)


@router.post("/controls/caps", response_class=HTMLResponse)
def set_caps(request: Request, daily: str = Form(""), monthly: str = Form(""),
             max_scan_price: str = Form(""), conn: sqlite3.Connection = Depends(get_conn)):
    values = [v.strip() or None for v in (daily, monthly, max_scan_price)]
    try:
        Budget(conn, request.app.state.cfg, local_now).set_caps(*values)
    except ValueError as e:
        return _header_response(request, conn, caps_error=str(e), status_code=400)
    return _header_response(request, conn)


@router.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    started = request.app.state.runner.start_refresh()
    status = "Refreshing…" if started else "Refresh already running"
    return _status_response(request, conn, refresh_status=status)


@router.post("/listing/{listing_id}/dismiss")
def dismiss(listing_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    db.dismiss_listing(conn, listing_id)
    return Response(status_code=200)


# --- deals page ----------------------------------------------------------------


def _product_names(conn: sqlite3.Connection, cfg: Config) -> dict[str, str]:
    catalog = _catalog(conn, cfg)
    return {p.id: p.name for p in catalog.products} if catalog is not None else {}


def _breakdown(row: sqlite3.Row, cfg: Config) -> str:
    price = row["price"]
    pct = (row["buyer_premium_pct"] if row["buyer_premium_pct"] is not None
           else cfg.buyer_premium_pct.get(row["source"], 0.0))
    premium = price * pct
    handling = cfg.handling_fee.get(row["source"], 0.0)
    tax = price * cfg.tax_rate
    shipping = row["total_cost"] - price - premium - handling - tax
    parts = [f"price ${price:,.2f}",
             f"shipping ${shipping:,.2f}" + (" (estimated)" if row["shipping_estimated"] else "")]
    if premium:
        parts.append(f"buyer premium ${premium:,.2f} ({pct:.0%})")
    if handling:
        parts.append(f"handling ${handling:,.2f}")
    if tax:
        parts.append(f"tax ${tax:,.2f}")
    return " + ".join(parts)


def _value(conn: sqlite3.Connection, row: sqlite3.Row) -> dict | None:
    if row["condition"] is None:
        return None
    return _market(db.get_market_value(conn, row["key"], row["condition"]))


def _market(mv: MarketValue | None) -> dict | None:
    """A stored value with its confidence badge."""
    if mv is None:
        return None
    basis = "sold" if mv.method in ("sold", "sold_average") else "listed"
    return {
        "value": mv.value,
        "badge": f"{mv.comp_count} {basis}" + (" · low" if mv.low_confidence else ""),
        "low": mv.low_confidence,
        "method": mv.method,
        "at": mv.computed_at,
    }


def _deal(conn: sqlite3.Connection, cfg: Config, row: sqlite3.Row, names: dict[str, str],
          new_since: datetime | None, now: datetime) -> dict:
    images = json.loads(row["image_urls"] or "[]")
    first_seen = _parse_time(row["first_seen"])
    value = db.get_market_value(conn, row["key"], row["condition"]) if row["condition"] else None
    return {
        "id": row["id"],
        "title": row["title"],
        "url": _http_url(row["url"]),
        "image": _http_url(images[0]) if images else None,
        "product": names.get(row["key"], row["key"]),
        "condition": row["condition"] or "—",
        "site": SITE_NAMES.get(row["source"], row["source"]),
        "total_cost": row["total_cost"],
        "price": row["price"],
        "rating": compare_deal(value, row["total_cost"], row["profit"], 1, cfg, now),
        "breakdown": _breakdown(row, cfg) if row["total_cost"] is not None else "",
        "market": _value(conn, row),
        "profit": row["profit"],
        "discount": row["discount_pct"],
        "time_left": _time_left(row["end_time"], now),
        "target": bool(row["is_target_hit"]),
        "new": new_since is None or (first_seen is not None and first_seen > new_since),
        "snapshot_at": (_parse_time(row["last_seen"]).astimezone().strftime("%Y-%m-%d %H:%M")
                        if row["source"] == "hibid" else None),
    }


def _hours(value: str | None) -> float | None:
    try:
        hours = float(value) if value else None
    except ValueError:
        return None
    return hours if hours is not None and math.isfinite(hours) and hours >= 0 else None


@router.get("/", response_class=HTMLResponse)
def deals(request: Request, category: str | None = None, source: str | None = None,
          ending_within_h: str | None = None, new_only: bool = False,
          target_only: bool = False, show_all: bool = True,
          conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    now = utc_now()
    partial = _is_partial(request)
    if partial:
        new_since = _parse_time(db.get_setting(conn, NEW_SINCE))
    else:
        previous = db.get_setting(conn, LAST_VISIT)
        new_since = _parse_time(previous)
        db.set_setting(conn, NEW_SINCE, previous or "")
    filters = {
        "category": category.strip().casefold() if category and category.strip() else None,
        "source": source or None,
        "ending_within_h": _hours(ending_within_h),
        "new_only": new_only,
        "target_only": target_only,
        "show_all": show_all,
    }
    rows = db.query_listings(
        conn, ["single"], now,
        category=filters["category"], source=filters["source"],
        ending_within_h=filters["ending_within_h"],
        new_since=new_since if new_only else None, target_only=target_only,
        deals_only=not show_all,
        stale_before=stale_before(cfg, now), hibid_before=hibid_snapshot_before(cfg, now))
    names = _product_names(conn, cfg)
    ctx = {"deals": [_deal(conn, cfg, r, names, new_since, now) for r in rows]}
    if partial:
        return _render(request, "partials/listing_rows.html", ctx)
    db.set_setting(conn, LAST_VISIT, now.isoformat())
    ctx.update(_header(request, conn))
    ctx.update(filters=filters, categories=CATEGORIES, sites=SITE_NAMES)
    return _render(request, "deals.html", ctx)


# --- lot pages -----------------------------------------------------------------


def _bulk(catalog: Catalog | None, bulk_id: str) -> BulkProduct | None:
    return next((b for b in catalog.bulk if b.id == bulk_id), None) if catalog else None


def _platform_names(catalog: Catalog | None) -> dict[str, str]:
    return {p.id: p.name for p in catalog.platforms} if catalog is not None else {}


def _is_game_row(row: sqlite3.Row) -> bool:
    """Game lots, and unknown-quantity lots keyed by a platform (no category)."""
    return row["kind"] == "game_lot" or (row["kind"] == "scan_only" and row["category"] is None)


def _value_key(row: sqlite3.Row, catalog: Catalog | None) -> tuple[str, str] | None:
    """The market value a listing is scored against (§7, §10)."""
    if row["kind"] == "single":
        return (row["key"], row["condition"]) if row["condition"] else None
    if _is_game_row(row):
        return row["key"], "per_game"
    bulk = _bulk(catalog, row["key"])
    if bulk is None:
        return None
    return bulk.source_product_id or bulk.id, "used"


def _per_unit(row: sqlite3.Row) -> float | None:
    if row["total_cost"] is None or not row["quantity"]:
        return None
    return row["total_cost"] / row["quantity"]


def _lot(conn: sqlite3.Connection, cfg: Config, row: sqlite3.Row, catalog: Catalog | None,
         now: datetime, find: bool) -> dict:
    images = json.loads(row["image_urls"] or "[]")
    key = _value_key(row, catalog)
    value = db.get_market_value(conn, *key) if key else None
    lot = {
        "id": row["id"],
        "title": row["title"],
        "url": _http_url(row["url"]),
        "image": _http_url(images[0]) if images else None,
        "site": SITE_NAMES.get(row["source"], row["source"]),
        "quantity": row["quantity"],
        "total_cost": row["total_cost"],
        "price": row["price"],
        "rating": compare_deal(value, row["total_cost"], row["profit"], row["quantity"], cfg, now, lot=True),
        "breakdown": _breakdown(row, cfg) if row["total_cost"] is not None else "",
        "per_unit": _per_unit(row),
        "market": _market(value),
        "profit": row["profit"],
        "discount": row["discount_pct"],
        "time_left": _time_left(row["end_time"], now),
        "find": find,
        "snapshot_at": (_parse_time(row["last_seen"]).astimezone().strftime("%Y-%m-%d %H:%M")
                        if row["source"] == "hibid" else None),
    }
    if _is_game_row(row):
        lot["product"] = _platform_names(catalog).get(row["key"], row["key"])
    else:
        bulk = _bulk(catalog, row["key"])
        lot["product"] = bulk.name if bulk else row["key"]
        lot["repair_cost"] = bulk.repair_cost if bulk else None
        lot["success_rate"] = bulk.success_rate if bulk else None
    return lot


@router.get("/lots/hardware", response_class=HTMLResponse)
def hardware_lots(request: Request, photo_finds_only: bool = False, show_all: bool = True,
                  conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    now = utc_now()
    rows = db.query_listings(conn, ["hardware_lot", "scan_only"], now,
                             photo_finds_only=photo_finds_only,
                             deals_only=not show_all,
                             stale_before=stale_before(cfg, now),
                             hibid_before=hibid_snapshot_before(cfg, now))
    catalog = _catalog(conn, cfg)
    ctx = {"lots": [_lot(conn, cfg, r, catalog, now, bool(r["photo_find"]))
                    for r in rows if not _is_game_row(r)]}
    if _is_partial(request):
        return _render(request, "partials/hardware_rows.html", ctx)
    ctx.update(_header(request, conn))
    ctx.update(filters={"photo_finds_only": photo_finds_only, "show_all": show_all})
    return _render(request, "hardware_lots.html", ctx)


@router.get("/lots/games", response_class=HTMLResponse)
def game_lots(request: Request, platform: str | None = None,
              ending_within_h: str | None = None, photo_finds_only: bool = False,
              show_all: bool = True,
              conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    now = utc_now()
    filters = {
        "platform": platform or None,
        "ending_within_h": _hours(ending_within_h),
        "photo_finds_only": photo_finds_only,
    }
    rows = db.query_listings(conn, ["game_lot", "scan_only"], now, **filters,
                             deals_only=not show_all,
                             stale_before=stale_before(cfg, now),
                             hibid_before=hibid_snapshot_before(cfg, now))
    filters["show_all"] = show_all
    catalog = _catalog(conn, cfg)
    ctx = {"lots": [_lot(conn, cfg, r, catalog, now, bool(r["photo_find"]))
                    for r in rows if _is_game_row(r)]}
    if _is_partial(request):
        return _render(request, "partials/game_rows.html", ctx)
    ctx.update(_header(request, conn))
    ctx.update(filters=filters, platforms=_platform_names(catalog))
    return _render(request, "game_lots.html", ctx)


# --- listing detail and manual scans -------------------------------------------

KIND_LABELS = {"single": "Single item", "hardware_lot": "Hardware lot", "game_lot": "Game lot",
               "scan_only": "Lot, quantity unknown"}


def _scan_view(scan: sqlite3.Row) -> dict:
    """A stored scan: its photos (the exact list sent to the scanner) and items, each
    item pointing at its photo when the index is known and the URL is safe."""
    urls = json.loads(scan["photo_urls"] or "[]")
    photos = [{"index": i, "url": _http_url(u)} for i, u in enumerate(urls)]
    items = []
    for it in json.loads(scan["items"] or "[]"):
        index = it.get("photo_index")
        valid = (isinstance(index, int) and 0 <= index < len(photos)
                 and photos[index]["url"] is not None)
        items.append({
            "title": it.get("title") or "",
            "edition": it.get("edition"),
            "platform": it.get("platform") or "",
            "completeness": it.get("completeness") or "unknown",
            "quantity": it.get("quantity"),
            "confidence": it.get("confidence") or "",
            "value": it.get("value"),
            "value_low": bool(it.get("value_low_confidence")),
            "photo": index if valid else None,
        })
    return {
        "status": scan["status"],
        "error": scan["error"],
        "model": scan["model"],
        "cost": scan["cost_usd"],
        "scanned_at": _parse_time(scan["scanned_at"]),
        "unit_count_estimate": scan["unit_count_estimate"],
        "identified_value": scan["identified_value"],
        "rare": bool(scan["is_rare_find"]),
        "lot_find": bool(scan["is_lot_find"]),
        "photos": [p for p in photos if p["url"] is not None],
        "found": items,
    }


@router.get("/listing/{listing_id}", response_class=HTMLResponse)
def listing_detail(request: Request, listing_id: int,
                   conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    row = db.get_listing(conn, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")
    now = utc_now()
    catalog = _catalog(conn, cfg)
    scan = db.get_scan(conn, listing_id)
    find = scan is not None and bool(scan["is_rare_find"] or scan["is_lot_find"])
    if row["kind"] == "single":
        names = {p.id: p.name for p in catalog.products} if catalog else {}
        d = {**_deal(conn, cfg, row, names, None, now), "new": False, "find": find,
             "per_unit": None, "quantity": None}
    else:
        d = _lot(conn, cfg, row, catalog, now, find)
    key = _value_key(row, catalog)
    comps = [{"title": c.title, "price": c.price, "sold_date": c.sold_date,
              "url": _http_url(c.url), "condition": c.condition_label}
             for c in (db.get_comps(conn, *key) if key else [])]
    end = _parse_time(row["end_time"])
    ended = end is not None and end < now
    cutoff = hibid_snapshot_before(cfg, now)
    snapshot_expired = (row["source"] == "hibid" and cutoff is not None
                        and _parse_time(row["last_seen"]) <= cutoff)
    ctx = {
        "d": d,
        "row": row,
        "kind": KIND_LABELS.get(row["kind"], row["kind"]),
        "game": _is_game_row(row),
        "value_basis": key[1] if key else None,
        "comps": comps,
        "ended": ended,
        "dismissed": bool(row["dismissed"]),
        "scan": _scan_view(scan) if scan is not None else None,
        "snapshot_expired": snapshot_expired,
        "can_scan": (row["kind"] in SCAN_KINDS and not row["dismissed"] and not snapshot_expired
                     and not ended and scan is None
                     and (end is not None
                          or _parse_time(row["last_seen"]) >= stale_before(cfg, now))),
        "scanner_available": bool(cfg.anthropic_api_key),
    }
    ctx.update(_header(request, conn))
    return _render(request, "listing.html", ctx)


@router.post("/listing/{listing_id}/scan", response_class=HTMLResponse)
def scan_listing(request: Request, listing_id: int,
                 conn: sqlite3.Connection = Depends(get_conn)):
    """Scan on demand: ignores the on/off switch and the price filter, not the caps
    (§11.2). The pipeline re-checks everything under the scan lock."""
    cfg = request.app.state.cfg
    row = db.get_listing(conn, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")
    now = utc_now()
    end = _parse_time(row["end_time"])
    cutoff = hibid_snapshot_before(cfg, now)
    unavailable = (row["dismissed"] or row["kind"] not in SCAN_KINDS
                   or (row["source"] == "hibid" and cutoff is not None
                       and _parse_time(row["last_seen"]) <= cutoff)
                   or (end is not None and end <= now)
                   or (end is None and _parse_time(row["last_seen"]) < stale_before(cfg, now)))
    if unavailable or db.get_scan(conn, listing_id) is not None:
        return _render(request, "partials/scan_status.html", {"error": True,
                       "message": "Not scanned: this listing is ineligible or already scanned."})
    decision = Budget(conn, cfg, local_now).can_manual_scan()
    if not decision.allowed:
        return _render(request, "partials/scan_status.html",
                       {"error": True, "message": f"Not scanned: {decision.reason}"})
    if not cfg.anthropic_api_key:
        return _render(request, "partials/scan_status.html", {"error": True,
                       "message": "Not scanned: ANTHROPIC_API_KEY is not set."})
    request.app.state.runner.start_scan(listing_id)
    return _render(request, "partials/scan_status.html",
                   {"error": False, "message": "Scanning… reload this page in a minute"
                                               " to see the results."})


# --- Keyless eBay browser helper -----------------------------------------------


@router.get("/sources/ebay", response_class=HTMLResponse)
def ebay_browser_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    ctx = _header(request, conn)
    ctx.update(helper=ebay_browser.browser_status(conn),
               helper_token=request.app.state.browser_token,
               api_mode=bool(request.app.state.cfg.ebay_client_id and request.app.state.cfg.ebay_client_secret))
    response = _render(request, "ebay_browser.html", ctx)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/sources/ebay/helper.zip")
def ebay_helper_download():
    buffer = io.BytesIO()
    folder = Path(__file__).resolve().parents[2] / "browser-helper"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix in (".js", ".json", ".md"):
                archive.writestr(file.name, file.read_bytes())
    return Response(buffer.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="dealfinder-browser-helper.zip"'})


def _import_ebay(runner, conn, html: str, captured_at: datetime) -> tuple[int, int, int]:
    page = ebay_browser.parse_active_page(html, captured_at)
    matched = runner.build_pipeline(conn).import_listings(page.listings)
    return page.cards, len(page.listings), matched


def _locked_ebay_import(request, conn, html, at, kind="active", query=""):
    with request.app.state.browser_lock:
        if kind == "sold":
            count = ebay_sold_browser.save_sold_capture(conn, query, html, at)
            return count, count, 0
        if kind != "active":
            raise ValueError("Unknown import type")
        return _import_ebay(request.app.state.runner, conn, html, at)


@router.post("/sources/ebay/import", response_class=HTMLResponse)
async def ebay_manual_import(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if int(request.headers.get("content-length", "0")) > ebay_browser.MAX_PAGE_BYTES + 65536:
        raise HTTPException(413, "eBay import exceeds 2 MB")
    try:
        async with request.form(max_files=0, max_fields=4, max_part_size=ebay_browser.MAX_PAGE_BYTES) as form:
            html = str(form.get("page_html", ""))
            kind = str(form.get("capture_kind", "active"))
            query = str(form.get("query", ""))
        cards, usable, matched = await run_in_threadpool(
            _locked_ebay_import, request, conn, html, utc_now(), kind, query)
        if kind == "sold":
            request.app.state.runner.start_refresh()
            return HTMLResponse(f"Imported {usable} sold comparisons. Recalculating listing values…",
                                headers={"HX-Trigger": "listings-updated"})
        return HTMLResponse(f"Imported: {cards} cards, {usable} usable listings, {matched} catalog matches.",
                            headers={"HX-Trigger": "listings-updated"})
    except ValueError as error:
        return _render(request, "partials/scan_status.html",
                       {"error": True, "message": f"Not imported: {error}"}, 400)


@router.post("/browser/ebay/next")
def ebay_helper_next(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    with request.app.state.browser_lock:
        if Budget(conn, cfg, local_now).paused():
            return {"paused": True, "reason": "Automatic checks paused"}
        catalog = load_catalog(cfg.catalog_dir, conn, cfg)
        queries = ebay_queries(catalog)
        sold_queries = ebay_sold_browser.needed_queries(conn, catalog, cfg, utc_now())
        api_mode = bool(cfg.ebay_client_id and cfg.ebay_client_secret)
        if api_mode and not sold_queries:
            return {"paused": True, "reason": "eBay API search configured; no sold captures needed"}
        if not queries:
            return {"paused": True, "reason": "No enabled catalog searches"}
        try:
            index = int(db.get_setting(conn, ebay_browser.ROTATION_KEY, "0"))
        except ValueError:
            index = 0
        kind = "sold" if sold_queries and (api_mode or index % 2 == 1) else "active"
        if kind == "sold":
            query = sold_queries[(index // 2) % len(sold_queries)]
            url = sold_url(query)
        else:
            active_index = int(db.get_setting(conn, "ebay_browser_active_rotation", "0"))
            query = queries[active_index % len(queries)]
            db.set_setting(conn, "ebay_browser_active_rotation", str(active_index + 1))
            url = ebay_browser.search_link(query)
        db.set_setting(conn, ebay_browser.ROTATION_KEY, str(index + 1))
        status = {**ebay_browser.browser_status(conn), "connected_at": utc_now().isoformat(),
                  "query": query, "kind": kind, "error": "", "state": "searching"}
        db.set_setting(conn, ebay_browser.STATUS_KEY, json.dumps(status))
        return {"query": query, "url": url, "kind": kind,
                "position": index % len(queries) + 1, "total": len(queries)}


def _save_ebay_capture(request, conn, data):
    at = datetime.fromisoformat(data.get("captured_at", ""))
    if at.tzinfo is None or not -timedelta(seconds=30) <= utc_now() - at <= timedelta(minutes=2):
        raise ValueError("Capture must be from the last two minutes")
    with request.app.state.browser_lock:
        if data.get("error"):
            status = {"state": "needs_attention", "connected_at": utc_now().isoformat(),
                      "error": str(data["error"])[:200], "query": str(data.get("query", ""))[:200]}
        else:
            html = data.get("html", "")
            if not isinstance(html, str):
                raise ValueError("Expected rendered listing HTML")
            kind = data.get("kind", "active")
            if kind not in ("active", "sold"):
                raise ValueError("Unknown eBay capture kind")
            if kind == "sold":
                usable = ebay_sold_browser.save_sold_capture(conn, str(data.get("query", "")), html, at)
                cards, matched = usable, 0
            else:
                cards, usable, matched = _import_ebay(request.app.state.runner, conn, html, at)
            status = {"state": "ready", "captured_at": at.isoformat(),
                      "connected_at": utc_now().isoformat(), "cards": cards,
                      "usable": usable, "matched": matched, "kind": kind, "query": str(data.get("query", ""))[:200]}
            if kind == "active":
                run = db.start_run(conn, "ebay", at)
                db.finish_run(conn, run, "ok", usable, "Keyless browser capture", utc_now())
        db.set_setting(conn, ebay_browser.STATUS_KEY, json.dumps(status))
    return status


@router.post("/browser/ebay/results")
async def ebay_helper_results(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if int(request.headers.get("content-length", "0")) > ebay_browser.MAX_PAGE_BYTES + 65536:
        raise HTTPException(413, "eBay import exceeds 2 MB")
    try:
        if len(await request.body()) > ebay_browser.MAX_PAGE_BYTES + 65536:
            raise HTTPException(413, "eBay import exceeds 2 MB")
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Expected listing capture object")
        status = await run_in_threadpool(_save_ebay_capture, request, conn, data)
        if data.get("kind") == "sold" and status.get("state") == "ready":
            request.app.state.runner.start_refresh()
        return {"ok": True, **status}
    except (ValueError, TypeError) as error:
        return JSONResponse({"error": str(error)[:200]}, status_code=400)


# --- HiBid browser-page imports ------------------------------------------------


@router.get("/sources/hibid", response_class=HTMLResponse)
def hibid_browser_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    ctx = _header(request, conn)
    ctx.update(snapshot=snapshot_status(conn, cfg, utc_now()), mode=cfg.hibid_mode,
               snapshot_minutes=cfg.hibid_snapshot_minutes,
               captured_default=local_now().strftime("%Y-%m-%dT%H:%M"),
               searches=[{"query": q, "url": search_link(q)}
                         for q in cfg.broad_queries.get("hibid", [])])
    return _render(request, "hibid_browser.html", ctx)


def _import_hibid(runner, conn, html: str, query: str, captured_at: datetime) -> int:
    with IMPORT_LOCK:
        page = parse_browser_page(html, captured_at)
        if not page.listings:
            raise ValueError("No usable USD lots with shipping and a closing countdown. "
                             + "; ".join(f"{v} {k}" for k, v in page.skipped.items()))
        pipeline = runner.build_pipeline(conn)
        matched = pipeline.import_listings(page.listings)
        save_snapshot(conn, query, page, captured_at, matched)
        run_id = db.start_run(conn, "hibid", captured_at)
        db.finish_run(conn, run_id, "ok", len(page.listings), "Browser page import", utc_now())
        return matched


@router.post("/sources/hibid/import", response_class=HTMLResponse)
async def import_hibid_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    if cfg.hibid_mode != "browser":
        raise HTTPException(status_code=400, detail="Enable hibid_mode: browser in config.yaml")
    if int(request.headers.get("content-length", "0")) > MAX_PAGE_BYTES:
        raise HTTPException(status_code=413, detail="HiBid import must be at most 5 MB")
    form = None
    try:
        form = await request.form(max_files=1, max_fields=6, max_part_size=MAX_PAGE_BYTES + 1)
        captured_at = datetime.fromisoformat(str(form.get("captured_at", ""))).astimezone(timezone.utc)
        age = utc_now() - captured_at
        if age < -timedelta(minutes=2) or age >= timedelta(minutes=cfg.hibid_snapshot_minutes):
            raise ValueError("Capture time must be recent and cannot be in the future. "
                             "Refresh HiBid and import its current results.")
        html = str(form.get("page_html") or "")
        upload = form.get("page_file")
        if not html and isinstance(upload, UploadFile):
            raw = await upload.read(MAX_PAGE_BYTES + 1)
            await upload.close()
            if len(raw) > MAX_PAGE_BYTES:
                raise HTTPException(status_code=413, detail="HiBid page exceeds 5 MB")
            html = raw.decode("utf-8-sig")
        if len(html.encode("utf-8")) > MAX_PAGE_BYTES:
            raise HTTPException(status_code=413, detail="HiBid page exceeds 5 MB")
        query = str(form.get("query") or "browser search")[:120]
        await run_in_threadpool(_import_hibid, request.app.state.runner, conn, html, query, captured_at)
    except (ValueError, UnicodeError) as e:
        return _render(request, "partials/scan_status.html",
                       {"error": True, "message": f"Not imported: {e}"}, status_code=400)
    finally:
        if form is not None:
            await form.close()
    response = HTMLResponse("Imported. Opening HiBid browser import status…")
    response.headers["HX-Redirect"] = "/sources/hibid"
    return response


# --- catalog ---------------------------------------------------------------------

CATALOG_TABLES = {"product": "product_overrides", "bulk": "bulk_overrides",
                  "platform": "platform_overrides"}
_FIELDS = {"product": ("max_price",), "bulk": ("repair_cost", "success_rate"),
           "platform": ()}
_CUSTOM_ID = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


def _number(value: str | None, label: str, high: float | None = None) -> float | None:
    """A blank field is None; anything else must be a finite number >= 0 (and <= high)."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        raise ValueError(f"{label} must be a number, got {text!r}") from None
    if not math.isfinite(number) or number < 0 or (high is not None and number > high):
        bound = f"between 0 and {high:g}" if high is not None else "a number ≥ 0"
        raise ValueError(f"{label} must be {bound}, got {text!r}")
    return number


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else ""


def _catalog_items(catalog: Catalog, table: str) -> list:
    return {"product": catalog.products, "bulk": catalog.bulk,
            "platform": catalog.platforms}[table]


def _catalog_row(conn: sqlite3.Connection, table: str, item, override: sqlite3.Row | None,
                 custom: bool) -> dict:
    """One editable catalog row. A numeric input holds the override (blank = none);
    its placeholder shows the value in effect without one."""
    row = {"table": table, "id": item.id, "name": item.name, "enabled": item.enabled,
           "category": getattr(item, "category", None), "custom": custom,
           "fields": {}, "markets": [], "error": None,
           "reference": _http_url(getattr(item, 'source_url', None))}
    for field in _FIELDS[table]:
        set_value = override[field] if override is not None else None
        in_effect = _fmt(getattr(item, field)) or "none"
        row["fields"][field] = {
            "value": _fmt(set_value),
            "placeholder": in_effect if set_value is None else "default",
        }
    if table == "product":
        row["markets"] = [(c, _market(db.get_market_value(conn, item.id, c)))
                         for c in ("new", "used")]
    elif table == "bulk":
        key = item.source_product_id or item.id
        row["markets"] = [("working used", _market(db.get_market_value(conn, key, "used")))]
        row["generated"] = item.source_product_id is not None
    else:
        row["markets"] = [("per game", _market(db.get_market_value(conn, item.id, "per_game")))]
    return row


def _catalog_rows(conn: sqlite3.Connection, catalog: Catalog, table: str,
                  only: str | None = None, *, visible: set[str] | None = None) -> list[dict]:
    overrides = db.get_overrides(conn, CATALOG_TABLES[table])
    custom = ({d["id"] for d in db.list_custom(conn, table)}
              if table in ("product", "bulk") else set())
    return [_catalog_row(conn, table, item, overrides.get(item.id), item.id in custom)
            for item in _catalog_items(catalog, table)
            if (only is None or item.id == only) and (visible is None or item.id in visible)]


def _custom_form(kind: str, values: dict | None = None, error: str | None = None,
                 message: str | None = None) -> dict:
    return {"kind": kind, "v": values or {}, "error": error, "message": message,
            "categories": CATEGORIES}


@router.get("/catalog", response_class=HTMLResponse)
def catalog_page(request: Request, q: str = '', page: int = Query(1, ge=1),
                 conn: sqlite3.Connection = Depends(get_conn)):
    catalog = _catalog(conn, request.app.state.cfg)
    q = q.strip()[:120]
    terms = edition_text(q).split()
    ctx = {"catalog_error": catalog is None, 'query': q, "sections": [], "forms": [
        _custom_form("product"), _custom_form("bulk")]}
    if catalog is not None:
        for table, title in (("product", "Products"), ("bulk", "Bulk products"),
                             ("platform", "Game platforms")):
            items = [item for item in _catalog_items(catalog, table)
                     if all(term in edition_text(item.name + ' ' + item.id + ' ' + item.match)
                            for term in terms)]
            if table == 'product':
                custom_ids = {row['id'] for row in db.list_custom(conn, 'product')}
                items.sort(key=lambda item: item.id not in custom_ids)
            total = len(items)
            pages = max(1, math.ceil(total / 200)) if table == 'product' else 1
            current = min(page, pages) if table == 'product' else 1
            if table == 'product':
                items = items[(current - 1) * 200:current * 200]
            ctx['sections'].append({
                'table': table, 'title': title, 'total': total, 'page': current, 'pages': pages,
                'previous': '/catalog?' + urlencode({'q': q, 'page': current - 1})
                            if current > 1 else None,
                'next': '/catalog?' + urlencode({'q': q, 'page': current + 1})
                        if current < pages else None,
                'rows': _catalog_rows(conn, catalog, table, visible={item.id for item in items}),
            })
    ctx.update(_header(request, conn))
    return _render(request, "catalog.html", ctx)


def _custom_entry(kind: str, form: dict[str, str], cfg: Config,
                  catalog: Catalog, conn: sqlite3.Connection) -> tuple[dict, set[str]]:
    """The validated custom entry and the ids it must not reuse; ValueError otherwise."""
    data = {k: form.get(k, "").strip() for k in ("id", "category", "name", "ebay_query", "match")}
    if not _CUSTOM_ID.match(data["id"]):
        raise ValueError("id must be lowercase letters, digits and hyphens, e.g. my-gpu-oc")
    if data["category"] not in CATEGORIES:
        raise ValueError(f"unknown category {data['category']!r}")
    bulk_ids = {b.id for b in catalog.bulk} | {d["id"] for d in db.list_custom(conn, "bulk")}
    if kind == "product":
        data["exclude"] = [p.strip() for p in form.get("exclude", "").splitlines() if p.strip()]
        data["max_price"] = _number(form.get("max_price"), "max price")
        product_ids = {p.id for p in catalog.products}
        generated = f"bulk-{data['id']}"  # gpu and cpu products are also bulk products
        if (data["id"] not in product_ids and data["category"] in ("gpu", "cpu")
                and generated in bulk_ids):
            raise ValueError(f"its bulk product id {generated!r} is already used")
        return data, product_ids
    default_cost, default_rate = cfg.repair_defaults[data["category"]]
    cost = _number(form.get("repair_cost"), "repair cost")
    rate = _number(form.get("success_rate"), "success rate", high=1.0)
    data["repair_cost"] = default_cost if cost is None else cost
    data["success_rate"] = default_rate if rate is None else rate
    return data, bulk_ids


@router.post("/catalog/custom/{kind}", response_class=HTMLResponse)
def add_custom_entry(request: Request, kind: str, entry_id: str = Form("", alias="id"),
                     category: str = Form(""), name: str = Form(""),
                     ebay_query: str = Form(""), match: str = Form(""),
                     exclude: str = Form(""), max_price: str = Form(""),
                     repair_cost: str = Form(""), success_rate: str = Form(""),
                     conn: sqlite3.Connection = Depends(get_conn)):
    if kind not in ("product", "bulk"):
        raise HTTPException(status_code=404, detail="unknown custom kind")
    form = {"id": entry_id, "category": category, "name": name, "ebay_query": ebay_query,
            "match": match, "exclude": exclude, "max_price": max_price,
            "repair_cost": repair_cost, "success_rate": success_rate}
    cfg = request.app.state.cfg
    catalog = _catalog(conn, cfg)
    try:
        if catalog is None:
            raise ValueError("the catalog failed to load; see the log")
        data, reserved = _custom_entry(kind, form, cfg, catalog, conn)
        db.add_custom(conn, kind, data, reserved_ids=reserved)
    except ValueError as e:
        return _render(request, "partials/custom_form.html",
                       _custom_form(kind, form, error=str(e)), status_code=400)
    response = _render(request, "partials/custom_form.html",
                       _custom_form(kind, message=f"Added {data['name']}."))
    response.headers["HX-Refresh"] = "true"  # reload so the new rows show
    return response


@router.post("/catalog/{table}/{item_id}", response_class=HTMLResponse)
def update_catalog(request: Request, table: str, item_id: str, enabled: bool = Form(False),
                   max_price: str = Form(""), repair_cost: str = Form(""),
                   success_rate: str = Form(""), conn: sqlite3.Connection = Depends(get_conn)):
    if table not in CATALOG_TABLES:
        raise HTTPException(status_code=404, detail="unknown catalog table")
    cfg = request.app.state.cfg
    catalog = _catalog(conn, cfg)
    if catalog is None:
        raise HTTPException(status_code=503, detail="the catalog failed to load")
    if item_id not in {i.id for i in _catalog_items(catalog, table)}:
        raise HTTPException(status_code=404, detail="unknown catalog entry")
    typed = {"max_price": max_price, "repair_cost": repair_cost, "success_rate": success_rate}
    fields: dict[str, object] = {"enabled": enabled}
    error = None
    try:
        if table == "product":
            fields["max_price"] = _number(max_price, "max price")
        elif table == "bulk":
            fields["repair_cost"] = _number(repair_cost, "repair cost")
            fields["success_rate"] = _number(success_rate, "success rate", high=1.0)
    except ValueError as e:
        error = str(e)
    else:
        db.set_override(conn, CATALOG_TABLES[table], item_id, **fields)
        catalog = _catalog(conn, cfg) or catalog
    [row] = _catalog_rows(conn, catalog, table, only=item_id)
    if error is not None:
        row["error"] = error
        row["enabled"] = enabled
        for field, cell in row["fields"].items():
            cell["value"] = typed[field]
    return _render(request, "partials/catalog_row.html", {"r": row},
                   status_code=400 if error else 200)
