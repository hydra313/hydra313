from __future__ import annotations

import logging
import re
import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Callable

from dealfinder import db
from dealfinder.catalog import Catalog
from dealfinder.config import Config
from dealfinder.lots import match_game_lot, match_hardware_lot
from dealfinder.matcher import GLOBAL_EXCLUDES, detect_condition, match_single
from dealfinder.models import (
    BulkProduct, Comp, Completeness, Condition, Listing, MarketValue, Platform, Product,
)
from dealfinder.sources.base import SourceError
from dealfinder.sources.ebay import EbaySource
from dealfinder.sources.ebay_sold import SoldFetcher
from dealfinder.text import normalize, parse_quantity

log = logging.getLogger(__name__)

# eBay condition labels (spec 7). Parts is checked first, then used/refurbished
# ("Pre-Owned · Like New" is used), then new.
_PARTS_LABEL = re.compile(r"\bparts\b")
_USED_LABEL = re.compile(r"\b(pre ?owned|used|refurbished)\b")
_NEW_LABEL = re.compile(r"\b(new|open box)\b")

# Spec 7, photo-identified items.
_ITEM_STOP_WORDS = {"the", "and", "for", "of"}
_ITEM_EXCLUDES = [
    r"\blots?\b",
    r"\bcase only\b",
    r"\bmanual only\b",
    r"\bbox only\b",
    r"\brepros?\b",
    r"\breproductions?\b",
    r"\bfor parts\b",
]
# Short title tokens that still tell games apart ("Halo 3", "Final Fantasy X").
_ROMAN_NUMERALS = {"ii", "iii", "iv", "v", "vi", "x"}
# A game's comps must not be hardware or merchandise that shares its name.
_GAME_EXCLUDES = re.compile(
    r"\b(consoles?|systems?|controllers?|helmets?|legendary edition|limited edition"
    r"|special edition|statues?|bundles?|\d+ ?gb|strategy guides?|steelbook only)\b"
)

# Values from current fixed-price listings expire sooner than sold values, so
# sold comps are retried soon after a block lifts.
ACTIVE_BIN_TTL = timedelta(hours=2)

# Turns one candidate (comp, eBay condition id for active listings) into the
# number it contributes to the median, or None to drop it.
ValueOf = Callable[[Comp, str | None], float | None]


def _iqr_bounds(values: Sequence[float]) -> tuple[float, float] | None:
    if len(values) < 4:
        return None
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def _iqr_keep(values: Sequence[float]) -> list[bool]:
    bounds = _iqr_bounds(values)
    if bounds is None:
        return [True] * len(values)
    lo, hi = bounds
    return [lo <= v <= hi for v in values]


def iqr_median(values: Sequence[float]) -> tuple[float, int] | None:
    kept = [v for v, keep in zip(values, _iqr_keep(values)) if keep]
    if not kept:
        return None
    return statistics.median(kept), len(kept)


def _is_parts(label: str | None) -> bool:
    return label is not None and _PARTS_LABEL.search(normalize(label)) is not None


def label_condition(label: str | None) -> Condition | None:
    if label is None:
        return None
    text = normalize(label)
    if _PARTS_LABEL.search(text):
        return None
    if _USED_LABEL.search(text):
        return "used"
    if _NEW_LABEL.search(text):
        return "new"
    return None


def _source_error(e: Exception) -> SourceError:
    error = SourceError(f"{type(e).__name__}: {e}")
    error.__cause__ = e
    return error


def _listing_comp(listing: Listing) -> Comp:
    return Comp(
        title=listing.title,
        price=listing.price + (listing.shipping or 0),
        sold_date=None,
        url=listing.url,
        condition_label=None,
    )


class MarketService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cfg: Config,
        sold: SoldFetcher,
        ebay: EbaySource | None,
        catalog: Catalog,
        clock: Callable[[], datetime],
    ) -> None:
        self._conn = conn
        self._cfg = cfg
        self._sold = sold
        self._ebay = ebay
        self._catalog = catalog
        self._clock = clock
        # Per-run memo of fetch results (or the SourceError raised), cleared by begin_run().
        self._sold_memo: dict[str, list[Comp] | SourceError] = {}
        self._bin_memo: dict[str, list[Listing] | SourceError] = {}
        # (table, key, condition) -> when a successful fetch last yielded no value.
        self._misses: dict[tuple[str, str, str], datetime] = {}

    def begin_run(self) -> None:
        """Start a refresh: forget memoized fetches (the negative cache stays)."""
        self._sold_memo.clear()
        self._bin_memo.clear()

    # --- public values ------------------------------------------------------

    def product_value(self, p: Product, condition: Condition) -> MarketValue | None:
        products = self._catalog.enabled_products()
        if not p.enabled:  # a bulk product can outlive its disabled source product
            products = [*products, replace(p, enabled=True)]

        def value_of(comp: Comp, condition_id: str | None) -> float | None:
            if _is_parts(comp.condition_label):
                return None
            m = match_single(comp.title, products, condition_id)
            if m is None or m[0].id != p.id:
                return None
            comp_condition = label_condition(comp.condition_label) or m[1]
            return comp.price if comp_condition == condition else None

        return self._value(p.id, condition, p.ebay_query, value_of, fallback=True)

    def bulk_value(self, b: BulkProduct) -> MarketValue | None:
        if b.source_product_id is not None:
            return self.product_value(self._catalog.product(b.source_product_id), "used")
        mask_patterns = self._catalog.mask_patterns()

        def value_of(comp: Comp, condition_id: str | None) -> float | None:
            if _is_parts(comp.condition_label):
                return None
            title_norm = normalize(comp.title)
            # The value of a working unit: lot matching allows broken titles, these don't.
            if any(re.search(p, title_norm) for p in GLOBAL_EXCLUDES):
                return None
            if parse_quantity(title_norm, mask_patterns) != 1:
                return None
            m = match_hardware_lot(title_norm, self._catalog.bulk, 1, 1,
                                   platforms=self._catalog.platforms)
            if m is None or m[0].id != b.id:
                return None
            comp_condition = (label_condition(comp.condition_label)
                              or detect_condition(title_norm, condition_id))
            return comp.price if comp_condition == "used" else None

        return self._value(b.id, "used", b.ebay_query, value_of, fallback=True)

    def per_game_value(self, platform: Platform) -> MarketValue | None:
        mask_patterns = self._catalog.mask_patterns()

        def value_of(comp: Comp, condition_id: str | None) -> float | None:
            if _is_parts(comp.condition_label):
                return None
            title_norm = normalize(comp.title)
            quantity = parse_quantity(title_norm, mask_patterns)
            m = match_game_lot(title_norm, self._catalog.platforms, quantity, 2)
            if m is None or m[0].id != platform.id or m[1] is None:
                return None
            return comp.price / m[1]

        return self._value(platform.id, "per_game", platform.ebay_lot_query, value_of,
                           fallback=False)

    def item_price(
        self, platform: str, title: str, completeness: Completeness, *, kind: str = "game"
    ) -> MarketValue | None:
        condition = "loose" if completeness == "unknown" else completeness
        query = f"{platform} {title}"
        if condition == "cib":
            query += " complete"
        elif condition == "sealed":
            query += " sealed"
        platform_re = re.compile(rf"\b{re.escape(normalize(platform))}\b")
        title_norm_item = normalize(title)
        word_res = []
        for w in re.findall(r"\w+", title_norm_item):
            if w in _ITEM_STOP_WORDS:
                continue
            if len(w) >= 3:
                word_res.append(re.compile(rf"\b{re.escape(w)}"))
            elif w.isdigit() or w in _ROMAN_NUMERALS:
                word_res.append(re.compile(rf"\b{re.escape(w)}\b"))
        game = kind == "game"

        def value_of(comp: Comp, condition_id: str | None) -> float | None:
            if _is_parts(comp.condition_label):
                return None
            title_norm = normalize(comp.title)
            if not platform_re.search(title_norm):
                return None
            if not all(r.search(title_norm) for r in word_res):
                return None
            if any(re.search(p, title_norm) for p in _ITEM_EXCLUDES):
                return None
            # Words the game's own title uses ("System Shock") do not exclude a comp.
            if game and any(m.group(0) not in title_norm_item
                            for m in _GAME_EXCLUDES.finditer(title_norm)):
                return None
            return comp.price

        return self._value(
            f"{normalize(platform)}|{title_norm_item}|{kind}", condition, query, value_of,
            fallback=False,
            table="item_prices",
            ttl=timedelta(days=self._cfg.item_price_ttl_days),
            min_comps=self._cfg.min_item_comps,
        )

    # --- shared machinery ---------------------------------------------------

    def _value(
        self,
        key: str,
        condition: str,
        query: str,
        value_of: ValueOf,
        *,
        fallback: bool,
        table: db.ValueTable = "market_values",
        ttl: timedelta | None = None,
        min_comps: int | None = None,
    ) -> MarketValue | None:
        ttl = timedelta(hours=self._cfg.market_ttl_hours) if ttl is None else ttl
        min_comps = self._cfg.min_comps if min_comps is None else min_comps
        now = self._clock()
        cached = db.get_market_value(self._conn, key, condition, table=table)
        if cached is not None:
            cached_ttl = ACTIVE_BIN_TTL if cached.method == "active_bin" else ttl
            if now - cached.computed_at < cached_ttl:
                return cached
        miss_key = (table, key, condition)
        missed_at = self._misses.get(miss_key)
        if missed_at is not None and now - missed_at < ttl:
            return None

        # A failed fetch returns the cached value, even a stale one, without
        # re-saving it, so the next run tries again.
        errored = False
        method = "sold"
        try:
            kept = self._select([(c, None) for c in self._fetch_sold(query)], value_of)
        except SourceError as e:
            log.warning("sold comps for %r failed: %s", query, e)
            if cached is not None:
                return cached
            errored, kept = True, []
        if not kept and fallback and self._ebay is not None:
            method = "active_bin"
            try:
                listings = self._fetch_bin(query)
            except SourceError as e:
                log.warning("active listings for %r failed: %s", query, e)
                if cached is not None:
                    return cached
                errored, listings = True, []
            kept = self._select([(_listing_comp(l), l.condition_id) for l in listings], value_of)
        if not kept:
            if not errored:
                self._misses[miss_key] = now
            return None

        keep = _iqr_keep([v for _, v in kept])
        kept = [pair for pair, k in zip(kept, keep) if k]
        values = [v for _, v in kept]
        mv = MarketValue(
            key=key,
            condition=condition,
            value=round(statistics.median(values), 2),
            comp_count=len(kept),
            method=method,
            low_confidence=method == "active_bin" or len(kept) < min_comps,
            computed_at=now,
        )
        db.save_market_value(self._conn, mv, [c for c, _ in kept], table=table)
        self._misses.pop(miss_key, None)
        return mv

    def _fetch_sold(self, query: str) -> list[Comp]:
        if query not in self._sold_memo:
            try:
                self._sold_memo[query] = self._sold.fetch(query)
            except SourceError as e:
                self._sold_memo[query] = e
            except Exception as e:  # noqa: BLE001 - a parse bug is a failed fetch
                self._sold_memo[query] = _source_error(e)
        result = self._sold_memo[query]
        if isinstance(result, SourceError):
            raise result
        return result

    def _fetch_bin(self, query: str) -> list[Listing]:
        assert self._ebay is not None
        if query not in self._bin_memo:
            try:
                self._bin_memo[query] = self._ebay.search(query, fixed_price_only=True)
            except SourceError as e:
                self._bin_memo[query] = e
            except Exception as e:  # noqa: BLE001 - a parse bug is a failed fetch
                self._bin_memo[query] = _source_error(e)
        result = self._bin_memo[query]
        if isinstance(result, SourceError):
            raise result
        return result

    @staticmethod
    def _select(
        candidates: list[tuple[Comp, str | None]], value_of: ValueOf
    ) -> list[tuple[Comp, float]]:
        out = []
        for comp, condition_id in candidates:
            value = value_of(comp, condition_id)
            if value is not None:
                out.append((comp, value))
        return out
