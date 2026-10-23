"""Dashboard routes: Deals page, header controls, refresh and dismiss (§14)."""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response

from dealfinder import db
from dealfinder.budget import Budget
from dealfinder.catalog import load_catalog
from dealfinder.config import Config
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
    return url if url and url.startswith(("https://", "http://")) else None


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


def _banners(conn: sqlite3.Connection, cfg: Config, now: datetime) -> list[dict]:
    banners = []
    for run in db.latest_runs(conn):
        site = SITE_NAMES.get(run["source"], run["source"])
        if run["status"] == "error":
            error = (run["error"] or "unknown error")[:_ERROR_MAX]
            banners.append({"level": "warn", "text": f"{site}: last run failed ({error})"})
        elif run["status"] == "site_change":
            banners.append({"level": "warn",
                            "text": f"{site}: probable site change, the last run found"
                                    " no listings"})
    failures = db.notification_failures_since(conn, now - timedelta(hours=24))
    if failures:
        noun = "notification" if failures == 1 else "notifications"
        banners.append({"level": "warn",
                        "text": f"{failures} Discord {noun} failed in the last 24 hours"})
    if not (cfg.ebay_client_id and cfg.ebay_client_secret):
        banners.append({"level": "info",
                        "text": "eBay keys not set (EBAY_CLIENT_ID and EBAY_CLIENT_SECRET):"
                                " eBay search is off."})
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
    return {
        "refreshing": refreshing,
        "refresh_status": refresh_status or ("Refreshing…" if refreshing else ""),
        "scan_enabled": budget.scan_enabled(),
        "paused": budget.paused(),
        "spent_today": budget.spent_today(),
        "spent_month": budget.spent_this_month(),
        "caps": budget.caps(),
        "caps_error": caps_error,
        "last_success": _last_success(conn, now),
        "banners": _banners(conn, cfg, now),
    }


def _header_response(request: Request, conn: sqlite3.Connection,
                     caps_error: str | None = None, status_code: int = 200,
                     refresh_status: str | None = None):
    ctx = _header(request, conn, caps_error, refresh_status)
    return _render(request, "partials/header_controls.html", ctx, status_code)


@router.get("/header", response_class=HTMLResponse)
def header(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """The header partial; it keeps polling this route while a refresh runs."""
    return _header_response(request, conn)


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
    return _header_response(request, conn, refresh_status=status)


@router.post("/listing/{listing_id}/dismiss")
def dismiss(listing_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    db.dismiss_listing(conn, listing_id)
    return Response(status_code=200)


# --- deals page ----------------------------------------------------------------


def _product_names(conn: sqlite3.Connection, cfg: Config) -> dict[str, str]:
    try:
        catalog = load_catalog(cfg.catalog_dir, conn, cfg)
    except Exception:  # noqa: BLE001 - the page still renders with product ids
        log.exception("loading the catalog failed")
        return {}
    return {p.id: p.name for p in catalog.products}


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
    mv = db.get_market_value(conn, row["key"], row["condition"])
    if mv is None:
        return None
    basis = "sold" if mv.method == "sold" else "listed"
    return {
        "value": mv.value,
        "badge": f"{mv.comp_count} {basis}" + (" · low" if mv.low_confidence else ""),
        "low": mv.low_confidence,
    }


def _deal(conn: sqlite3.Connection, cfg: Config, row: sqlite3.Row, names: dict[str, str],
          new_since: datetime | None, now: datetime) -> dict:
    images = json.loads(row["image_urls"] or "[]")
    first_seen = _parse_time(row["first_seen"])
    return {
        "id": row["id"],
        "title": row["title"],
        "url": _http_url(row["url"]),
        "image": _http_url(images[0]) if images else None,
        "product": names.get(row["key"], row["key"]),
        "condition": row["condition"] or "—",
        "site": SITE_NAMES.get(row["source"], row["source"]),
        "total_cost": row["total_cost"],
        "breakdown": _breakdown(row, cfg) if row["total_cost"] is not None else "",
        "market": _value(conn, row),
        "profit": row["profit"],
        "discount": row["discount_pct"],
        "time_left": _time_left(row["end_time"], now),
        "target": bool(row["is_target_hit"]),
        "new": new_since is None or (first_seen is not None and first_seen > new_since),
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
          target_only: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
    cfg = request.app.state.cfg
    now = utc_now()
    partial = (request.headers.get("HX-Request") == "true"
               and request.headers.get("HX-History-Restore-Request") != "true")
    if partial:
        new_since = _parse_time(db.get_setting(conn, NEW_SINCE))
    else:
        previous = db.get_setting(conn, LAST_VISIT)
        new_since = _parse_time(previous)
        db.set_setting(conn, NEW_SINCE, previous or "")
    filters = {
        "category": category or None,
        "source": source or None,
        "ending_within_h": _hours(ending_within_h),
        "new_only": new_only,
        "target_only": target_only,
    }
    rows = db.query_listings(
        conn, ["single"], now,
        category=filters["category"], source=filters["source"],
        ending_within_h=filters["ending_within_h"],
        new_since=new_since if new_only else None, target_only=target_only)
    names = _product_names(conn, cfg)
    ctx = {"deals": [_deal(conn, cfg, r, names, new_since, now) for r in rows]}
    if partial:
        return _render(request, "partials/listing_rows.html", ctx)
    db.set_setting(conn, LAST_VISIT, now.isoformat())
    ctx.update(_header(request, conn))
    ctx.update(filters=filters, categories=CATEGORIES, sites=SITE_NAMES)
    return _render(request, "deals.html", ctx)
