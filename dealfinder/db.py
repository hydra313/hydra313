from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Collection, Literal, get_args

from dealfinder.models import (
    Category, Classified, Comp, Listing, MarketValue, ScanResult, Scored,
)

if TYPE_CHECKING:
    from dealfinder.finds import FindEval

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    category TEXT,
    condition TEXT,
    quantity INTEGER,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    image_urls TEXT NOT NULL DEFAULT '[]',
    price REAL NOT NULL,
    shipping REAL,
    shipping_offered INTEGER NOT NULL DEFAULT 1,
    shipping_estimated INTEGER NOT NULL DEFAULT 0,
    buyer_premium_pct REAL,
    handling_fee REAL,
    is_auction INTEGER NOT NULL DEFAULT 0,
    end_time TEXT,
    bid_count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    total_cost REAL,
    profit REAL,
    discount_pct REAL,
    is_target_hit INTEGER NOT NULL DEFAULT 0,
    is_deal INTEGER NOT NULL DEFAULT 0,
    rank_score REAL NOT NULL DEFAULT 0,
    UNIQUE (source, source_id)
);
CREATE TABLE IF NOT EXISTS market_values (
    key TEXT NOT NULL,
    condition TEXT NOT NULL,
    value REAL NOT NULL,
    comp_count INTEGER NOT NULL,
    method TEXT NOT NULL,
    low_confidence INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (key, condition)
);
CREATE TABLE IF NOT EXISTS comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    condition TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL,
    sold_date TEXT,
    url TEXT NOT NULL,
    condition_label TEXT
);
CREATE INDEX IF NOT EXISTS idx_comps_key ON comps (key, condition);
CREATE TABLE IF NOT EXISTS item_prices (
    platform TEXT NOT NULL,
    title TEXT NOT NULL,
    completeness TEXT NOT NULL,
    value REAL NOT NULL,
    comp_count INTEGER NOT NULL,
    method TEXT NOT NULL DEFAULT 'sold',
    low_confidence INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (platform, title, completeness)
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings (id),
    status TEXT NOT NULL,
    model TEXT,
    items TEXT NOT NULL DEFAULT '[]',
    unit_count_estimate INTEGER,
    identified_value REAL,
    is_rare_find INTEGER NOT NULL DEFAULT 0,
    is_lot_find INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    scanned_at TEXT NOT NULL,
    error TEXT,
    photo_urls TEXT NOT NULL DEFAULT '[]',
    UNIQUE (listing_id)
);
CREATE TABLE IF NOT EXISTS vision_spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    listing_id INTEGER,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings (id),
    reason TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE (listing_id, reason)
);
CREATE TABLE IF NOT EXISTS product_overrides (
    product_id TEXT PRIMARY KEY,
    enabled INTEGER,
    max_price REAL
);
CREATE TABLE IF NOT EXISTS bulk_overrides (
    bulk_id TEXT PRIMARY KEY,
    enabled INTEGER,
    repair_cost REAL,
    success_rate REAL
);
CREATE TABLE IF NOT EXISTS platform_overrides (
    platform_id TEXT PRIMARY KEY,
    enabled INTEGER
);
CREATE TABLE IF NOT EXISTS custom_products (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    ebay_query TEXT NOT NULL,
    match TEXT NOT NULL,
    exclude TEXT NOT NULL DEFAULT '[]',
    max_price REAL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS custom_bulk_products (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    ebay_query TEXT NOT NULL,
    match TEXT NOT NULL,
    repair_cost REAL NOT NULL,
    success_rate REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    source_product_id TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    listings_seen INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Timestamps are ISO-8601 UTC text whose fractional part may be absent, so
# time comparisons go through julianday() rather than comparing strings.
_PHOTO_FIND = "(COALESCE(s.is_rare_find, 0) = 1 OR COALESCE(s.is_lot_find, 0) = 1)"
# Only rows with no end time can go stale; takes the stale_before cutoff.
_NOT_STALE = "(l.end_time IS NOT NULL OR julianday(l.last_seen) >= julianday(?))"


def _iso(dt: datetime) -> str:
    """ISO-8601 UTC string; naive datetimes are taken as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def record_spend(
    conn: sqlite3.Connection,
    listing_id: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    at: datetime,
) -> None:
    conn.execute(
        "INSERT INTO vision_spend (scanned_at, listing_id, input_tokens, output_tokens, cost_usd)"
        " VALUES (?, ?, ?, ?, ?)",
        (_iso(at), listing_id, input_tokens, output_tokens, cost_usd),
    )
    conn.commit()


def spend_between(conn: sqlite3.Connection, start: datetime, end: datetime) -> float:
    """Total vision spend with start <= scanned_at < end."""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM vision_spend"
        " WHERE julianday(scanned_at) >= julianday(?) AND julianday(scanned_at) < julianday(?)",
        (_iso(start), _iso(end)),
    ).fetchone()
    return float(row["total"])


def upsert_listing(
    conn: sqlite3.Connection,
    listing: Listing,
    c: Classified,
    scored: Scored | None,
    now: datetime,
) -> int:
    ts = _iso(now)
    values = {
        "source": listing.source,
        "source_id": listing.source_id,
        "kind": c.kind,
        "key": c.key,
        "category": c.category,
        "condition": c.condition,
        "quantity": c.quantity,
        "title": listing.title,
        "url": listing.url,
        "image_urls": json.dumps(listing.image_urls),
        "price": listing.price,
        "shipping": listing.shipping,
        "shipping_offered": int(listing.shipping_offered),
        "shipping_estimated": int(scored.shipping_estimated) if scored else 0,
        "buyer_premium_pct": listing.buyer_premium_pct,
        "is_auction": int(listing.is_auction),
        "end_time": _iso(listing.end_time) if listing.end_time else None,
        "bid_count": listing.bid_count,
        "first_seen": ts,
        "last_seen": ts,
        "total_cost": scored.total_cost if scored else None,
        "profit": scored.profit if scored else None,
        "discount_pct": scored.discount_pct if scored else None,
        "is_target_hit": int(scored.is_target_hit) if scored else 0,
        "is_deal": int(scored.is_deal) if scored else 0,
        "rank_score": scored.rank_score if scored else 0.0,
    }
    cols = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    updates = ", ".join(
        f"{k} = excluded.{k}" for k in values if k not in ("source", "source_id", "first_seen")
    )
    row = conn.execute(
        f"INSERT INTO listings ({cols}) VALUES ({marks})"
        f" ON CONFLICT (source, source_id) DO UPDATE SET {updates}"
        " RETURNING id",
        values,
    ).fetchone()
    conn.commit()
    return row["id"]


def get_listing(conn: sqlite3.Connection, listing_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()


def query_listings(
    conn: sqlite3.Connection,
    kinds: Sequence[str],
    now: datetime,
    *,
    category: str | None = None,
    source: str | None = None,
    platform: str | None = None,
    ending_within_h: float | None = None,
    new_since: datetime | None = None,
    target_only: bool = False,
    photo_finds_only: bool = False,
    deals_only: bool = True,
    stale_before: datetime | None = None,
) -> list[sqlite3.Row]:
    """`stale_before` hides rows with no end time last seen before it: a listing no
    search returns any more (a sold or withdrawn Buy It Now) has no end time to expire
    it. Rows with an end time keep their normal ended check."""
    if not kinds:
        return []
    where = [
        f"l.kind IN ({', '.join('?' for _ in kinds)})",
        "l.dismissed = 0",
        "(l.end_time IS NULL OR julianday(l.end_time) >= julianday(?))",
    ]
    params: list[object] = [*kinds, _iso(now)]
    if category is not None:
        where.append("l.category = ?")
        params.append(category)
    if source is not None:
        where.append("l.source = ?")
        params.append(source)
    if platform is not None:
        where.append("l.key = ?")
        params.append(platform)
    if ending_within_h is not None:
        where.append("l.end_time IS NOT NULL AND julianday(l.end_time) <= julianday(?)")
        params.append(_iso(now + timedelta(hours=ending_within_h)))
    if new_since is not None:
        where.append("julianday(l.first_seen) > julianday(?)")
        params.append(_iso(new_since))
    if target_only:
        where.append("l.is_target_hit = 1")
    if photo_finds_only:
        where.append(_PHOTO_FIND)
    if deals_only:
        where.append(f"(l.is_deal = 1 OR {_PHOTO_FIND})")
    if stale_before is not None:
        where.append(_NOT_STALE)
        params.append(_iso(stale_before))
    sql = (
        f"SELECT l.*, CASE WHEN {_PHOTO_FIND} THEN 1 ELSE 0 END AS photo_find,"
        " COALESCE(s.is_rare_find, 0) AS is_rare_find,"
        " COALESCE(s.is_lot_find, 0) AS is_lot_find"
        " FROM listings l LEFT JOIN scans s ON s.listing_id = l.id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY l.is_target_hit DESC, photo_find DESC, l.rank_score DESC, l.id"
    )
    return conn.execute(sql, params).fetchall()


def dismiss_listing(conn: sqlite3.Connection, listing_id: int) -> None:
    conn.execute("UPDATE listings SET dismissed = 1 WHERE id = ?", (listing_id,))
    conn.commit()


def start_run(conn: sqlite3.Connection, source: str, now: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, source, status) VALUES (?, ?, 'running')",
        (_iso(now), source),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: Literal["ok", "error", "site_change"],
    listings_seen: int,
    error: str | None,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, listings_seen = ?, error = ? WHERE id = ?",
        (_iso(now), status, listings_seen, error, run_id),
    )
    conn.commit()


def latest_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY source)"
        " ORDER BY source"
    ).fetchall()


# --- market values and comps -------------------------------------------------

ValueTable = Literal["market_values", "item_prices"]


def _check_value_table(table: str) -> None:
    if table not in ("market_values", "item_prices"):
        raise ValueError(f"unknown market value table {table!r}")


def save_market_value(
    conn: sqlite3.Connection,
    mv: MarketValue,
    comps: list[Comp],
    table: ValueTable = "market_values",
) -> None:
    """Upsert a value and replace the comps stored for its (key, condition).

    item_prices rows split the key "platform|title" into their own columns.
    """
    _check_value_table(table)
    common = {
        "value": mv.value,
        "comp_count": mv.comp_count,
        "method": mv.method,
        "low_confidence": int(mv.low_confidence),
        "computed_at": _iso(mv.computed_at),
    }
    if table == "market_values":
        row = {"key": mv.key, "condition": mv.condition, **common}
        conflict = "key, condition"
    else:
        platform, _, title = mv.key.partition("|")
        row = {"platform": platform, "title": title, "completeness": mv.condition, **common}
        conflict = "platform, title, completeness"
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    updates = ", ".join(f"{k} = excluded.{k}" for k in common)
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({marks})"
        f" ON CONFLICT ({conflict}) DO UPDATE SET {updates}",
        row,
    )
    conn.execute("DELETE FROM comps WHERE key = ? AND condition = ?", (mv.key, mv.condition))
    conn.executemany(
        "INSERT INTO comps (key, condition, title, price, sold_date, url, condition_label)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (mv.key, mv.condition, c.title, c.price,
             c.sold_date.isoformat() if c.sold_date else None, c.url, c.condition_label)
            for c in comps
        ],
    )
    conn.commit()


def get_market_value(
    conn: sqlite3.Connection,
    key: str,
    condition: str,
    table: ValueTable = "market_values",
) -> MarketValue | None:
    _check_value_table(table)
    if table == "market_values":
        row = conn.execute(
            "SELECT * FROM market_values WHERE key = ? AND condition = ?", (key, condition)
        ).fetchone()
    else:
        platform, _, title = key.partition("|")
        row = conn.execute(
            "SELECT * FROM item_prices WHERE platform = ? AND title = ? AND completeness = ?",
            (platform, title, condition),
        ).fetchone()
    if row is None:
        return None
    return MarketValue(
        key=key,
        condition=condition,
        value=row["value"],
        comp_count=row["comp_count"],
        method=row["method"],
        low_confidence=bool(row["low_confidence"]),
        computed_at=datetime.fromisoformat(row["computed_at"]),
    )


def get_comps(conn: sqlite3.Connection, key: str, condition: str) -> list[Comp]:
    rows = conn.execute(
        "SELECT * FROM comps WHERE key = ? AND condition = ? ORDER BY id", (key, condition)
    ).fetchall()
    return [
        Comp(
            title=r["title"],
            price=r["price"],
            sold_date=date.fromisoformat(r["sold_date"]) if r["sold_date"] else None,
            url=r["url"],
            condition_label=r["condition_label"],
        )
        for r in rows
    ]


# --- catalog overrides and custom entries -----------------------------------

_OVERRIDE_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "product_overrides": ("product_id", ("enabled", "max_price")),
    "bulk_overrides": ("bulk_id", ("enabled", "repair_cost", "success_rate")),
    "platform_overrides": ("platform_id", ("enabled",)),
}
_CUSTOM_TABLES = {"product": "custom_products", "bulk": "custom_bulk_products"}
_CUSTOM_COLUMNS: dict[str, tuple[str, ...]] = {
    "product": ("id", "category", "name", "ebay_query", "match", "exclude", "max_price", "enabled"),
    "bulk": ("id", "category", "name", "ebay_query", "match", "repair_cost", "success_rate",
             "enabled", "source_product_id"),
}
_CUSTOM_REQUIRED: dict[str, tuple[str, ...]] = {
    "product": ("id", "category", "name", "ebay_query", "match"),
    "bulk": ("id", "category", "name", "ebay_query", "match", "repair_cost", "success_rate"),
}


def set_override(
    conn: sqlite3.Connection,
    table: Literal["product_overrides", "bulk_overrides", "platform_overrides"],
    item_id: str,
    **fields: object,
) -> None:
    """Upsert an override row, changing only the given fields. None clears a field."""
    if table not in _OVERRIDE_TABLES:
        raise ValueError(f"unknown override table {table!r}")
    id_col, allowed = _OVERRIDE_TABLES[table]
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"{table} has no field(s) {sorted(unknown)}")
    values = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items()}
    cols = ", ".join([id_col, *values])
    marks = ", ".join("?" for _ in range(len(values) + 1))
    if values:
        action = "DO UPDATE SET " + ", ".join(f"{k} = excluded.{k}" for k in values)
    else:
        action = "DO NOTHING"
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT ({id_col}) {action}",
        (item_id, *values.values()),
    )
    conn.commit()


def get_overrides(
    conn: sqlite3.Connection,
    table: Literal["product_overrides", "bulk_overrides", "platform_overrides"],
) -> dict[str, sqlite3.Row]:
    if table not in _OVERRIDE_TABLES:
        raise ValueError(f"unknown override table {table!r}")
    id_col = _OVERRIDE_TABLES[table][0]
    return {row[id_col]: row for row in conn.execute(f"SELECT * FROM {table}")}


def add_custom(
    conn: sqlite3.Connection,
    kind: Literal["product", "bulk"],
    data: dict,
    reserved_ids: Collection[str] = (),
) -> None:
    """Insert a user-defined catalog entry; ValueError if it fails validation.

    `reserved_ids` lets the caller add ids already taken elsewhere (e.g. seed entries).
    """
    if kind not in _CUSTOM_TABLES:
        raise ValueError(f"unknown custom kind {kind!r}")
    entry_id = data.get("id")
    label = f"custom {kind} {entry_id!r}"
    for key in _CUSTOM_REQUIRED[kind]:
        if data.get(key) is None or data.get(key) == "":
            raise ValueError(f"{label}: missing {key}")
    unknown = set(data) - set(_CUSTOM_COLUMNS[kind])
    if unknown:
        raise ValueError(f"{label}: unknown field(s) {sorted(unknown)}")
    if data["category"] not in get_args(Category):
        raise ValueError(f"{label}: unknown category {data['category']!r}")
    exclude = list(data.get("exclude") or []) if kind == "product" else []
    for pattern in [data["match"], *exclude]:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"{label}: invalid regex {pattern!r}: {e}") from e
    table = _CUSTOM_TABLES[kind]
    if entry_id in reserved_ids or conn.execute(
        f"SELECT 1 FROM {table} WHERE id = ?", (entry_id,)
    ).fetchone():
        raise ValueError(f"{label}: id already exists")
    values = dict(data)
    if kind == "product":
        values["exclude"] = json.dumps(exclude)
    if "enabled" in values:
        values["enabled"] = int(bool(values["enabled"]))
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
    conn.commit()


def list_custom(conn: sqlite3.Connection, kind: Literal["product", "bulk"]) -> list[dict]:
    if kind not in _CUSTOM_TABLES:
        raise ValueError(f"unknown custom kind {kind!r}")
    rows = conn.execute(f"SELECT * FROM {_CUSTOM_TABLES[kind]} ORDER BY rowid").fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["enabled"] = bool(d["enabled"])
        if kind == "product":
            d["exclude"] = json.loads(d["exclude"])
        out.append(d)
    return out


# --- notifications -----------------------------------------------------------


def claim_notification(
    conn: sqlite3.Connection, listing_id: int, reason: str, now: datetime
) -> bool:
    """Reserve the (listing, reason) notification; True only for the first caller.

    The row starts as 'pending' and stays claimed whatever happens next, so a
    failed send is never retried automatically.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO notifications (listing_id, reason, sent_at, status)"
        " VALUES (?, ?, ?, 'pending')",
        (listing_id, reason, _iso(now)),
    )
    conn.commit()
    return cur.rowcount == 1


def mark_notification(
    conn: sqlite3.Connection, listing_id: int, reason: str, status: str
) -> None:
    conn.execute(
        "UPDATE notifications SET status = ? WHERE listing_id = ? AND reason = ?",
        (status, listing_id, reason),
    )
    conn.commit()


def notification_failures_since(conn: sqlite3.Connection, since: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications"
        " WHERE status = 'failed' AND julianday(sent_at) >= julianday(?)",
        (_iso(since),),
    ).fetchone()
    return int(row["n"])


# --- photo scans -------------------------------------------------------------


def save_scan(
    conn: sqlite3.Connection,
    listing_id: int,
    result: ScanResult,
    eval: FindEval | None,
    now: datetime,
    *,
    photo_urls: Sequence[str],
) -> None:
    """Store a listing's scan. `photo_urls` is the exact list passed to the scanner,
    which each item's photo_index points into (-1 = unknown photo). Items carry the
    price they were valued at, when there is one."""
    priced = eval.priced if eval is not None else [(item, None) for item in result.items]
    items = [
        {**asdict(item),
         "value": price.value if price is not None else None,
         "value_low_confidence": price.low_confidence if price is not None else None}
        for item, price in priced
    ]
    conn.execute(
        "INSERT INTO scans (listing_id, status, model, items, unit_count_estimate,"
        " identified_value, is_rare_find, is_lot_find, cost_usd, scanned_at, error, photo_urls)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            listing_id, result.status, result.model, json.dumps(items),
            result.unit_count_estimate,
            eval.identified_value if eval is not None else None,
            int(eval.is_rare_find) if eval is not None else 0,
            int(eval.is_lot_find) if eval is not None else 0,
            result.cost_usd, _iso(now), result.error, json.dumps(list(photo_urls)),
        ),
    )
    conn.commit()


def get_scan(conn: sqlite3.Connection, listing_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scans WHERE listing_id = ?", (listing_id,)).fetchone()


def scan_failures_since(conn: sqlite3.Connection, since: datetime) -> list[int]:
    """Listing ids whose scan failed or was refused at or after `since`, newest first."""
    rows = conn.execute(
        "SELECT listing_id FROM scans WHERE status IN ('failed', 'refused')"
        " AND julianday(scanned_at) >= julianday(?) ORDER BY julianday(scanned_at) DESC, id DESC",
        (_iso(since),),
    ).fetchall()
    return [r["listing_id"] for r in rows]


def get_scan_by_source(
    conn: sqlite3.Connection, source: str, source_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT s.* FROM scans s JOIN listings l ON l.id = s.listing_id"
        " WHERE l.source = ? AND l.source_id = ?",
        (source, source_id),
    ).fetchone()


def scan_candidates(
    conn: sqlite3.Connection, now: datetime, stale_before: datetime | None = None
) -> list[sqlite3.Row]:
    """Spec 11.1: live, undismissed lots and unknown-quantity lots never scanned, best first.
    Rows with no end time last seen before `stale_before` are not live."""
    stale = "" if stale_before is None else f" AND {_NOT_STALE}"
    params = (_iso(now),) if stale_before is None else (_iso(now), _iso(stale_before))
    return conn.execute(
        "SELECT l.* FROM listings l"
        " WHERE l.kind IN ('hardware_lot', 'game_lot', 'scan_only')"
        " AND l.dismissed = 0"
        " AND (l.end_time IS NULL OR julianday(l.end_time) >= julianday(?))"
        f"{stale}"
        " AND NOT EXISTS (SELECT 1 FROM scans s WHERE s.listing_id = l.id)"
        " ORDER BY l.rank_score DESC, l.id",
        params,
    ).fetchall()
