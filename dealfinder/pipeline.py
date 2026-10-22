"""Refresh orchestration: search, classify, value, score, store, scan, notify (spec 9, 11, 12)."""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from dealfinder import db
from dealfinder.budget import Budget
from dealfinder.catalog import Catalog
from dealfinder.classify import classify
from dealfinder.config import Config
from dealfinder.finds import FindEval, evaluate_finds
from dealfinder.market import MarketService
from dealfinder.models import Classified, Listing, ScanResult, Scored
from dealfinder.notify import Notifier, Reason
from dealfinder.scoring import score_game_lot, score_hardware_lot, score_single, total_cost
from dealfinder.sources.base import Source
from dealfinder.vision import ScanDeferred, VisionScanner

log = logging.getLogger(__name__)

ROTATION_SETTING = "ebay_rotation_index"
SCAN_KINDS = ("hardware_lot", "game_lot", "scan_only")
_GATE_STOPS = ("scanning is off", "daily cap reached", "monthly cap reached")


@dataclass
class RefreshReport:
    per_source: dict[str, tuple[str, int]] = field(default_factory=dict)
    scanned: int = 0
    notified: int = 0


def ebay_queries(catalog: Catalog) -> list[str]:
    queries = [p.ebay_query for p in catalog.enabled_products()]
    queries += [f"{b.ebay_query} lot" for b in catalog.enabled_bulk()
                if b.source_product_id is None]
    queries += [p.ebay_lot_query for p in catalog.enabled_platforms()]
    return list(dict.fromkeys(queries))


def rotation_slice(queries: list[str], index: int, slices: int) -> list[str]:
    slices = max(1, slices)
    i = index % slices
    n = len(queries)
    return queries[i * n // slices:(i + 1) * n // slices]


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _row_listing(row: sqlite3.Row) -> Listing:
    return Listing(
        source=row["source"],
        source_id=row["source_id"],
        title=row["title"],
        url=row["url"],
        price=row["price"],
        shipping=row["shipping"],
        shipping_offered=bool(row["shipping_offered"]),
        image_urls=json.loads(row["image_urls"] or "[]"),
        is_auction=bool(row["is_auction"]),
        end_time=datetime.fromisoformat(row["end_time"]) if row["end_time"] else None,
        bid_count=row["bid_count"],
        buyer_premium_pct=row["buyer_premium_pct"],
    )


def _row_classified(row: sqlite3.Row) -> Classified:
    return Classified(kind=row["kind"], key=row["key"], condition=row["condition"],
                      quantity=row["quantity"], category=row["category"])


def _ended(row: sqlite3.Row, now: datetime) -> bool:
    return bool(row["end_time"]) and datetime.fromisoformat(row["end_time"]) < now


class Pipeline:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cfg: Config,
        catalog_loader: Callable[[], Catalog],
        sources: dict[str, Source],
        market: MarketService,
        scanner: VisionScanner | None,
        budget: Budget,
        notifier: Notifier | None,
        clock: Callable[[], datetime],
    ) -> None:
        self.conn = conn
        self.cfg = cfg
        self.catalog_loader = catalog_loader
        self.sources = sources
        self.market = market
        self.scanner = scanner
        self.budget = budget
        self.notifier = notifier
        self.clock = clock

    # --- refresh ------------------------------------------------------------

    def refresh(self) -> RefreshReport:
        report = RefreshReport()
        catalog = self.catalog_loader()
        self.market.begin_run()
        pending: dict[int, dict[Reason, list[str]]] = {}
        for name, source in self.sources.items():
            report.per_source[name] = self._run_source(name, source, catalog, pending)

        # Text deals are time-sensitive: alert before the (slow) photo scans.
        now = self.clock()
        for listing_id, reasons in pending.items():
            row = db.get_listing(self.conn, listing_id)
            if row is None or row["dismissed"] or _ended(row, now):
                continue
            for reason, details in reasons.items():
                if self._notify(listing_id, reason, details):
                    report.notified += 1

        if self.scanner is not None:
            report.scanned, photo_finds = self._auto_scan(catalog)
            report.notified += photo_finds
        return report

    def _queries(self, name: str, catalog: Catalog) -> list[str]:
        if name != "ebay":
            return list(self.cfg.broad_queries.get(name, []))
        try:
            index = int(db.get_setting(self.conn, ROTATION_SETTING, "0") or 0)
        except ValueError:
            index = 0
        slices = math.ceil(self.cfg.ebay_full_pass_hours * 60 / self.cfg.refresh_minutes)
        db.set_setting(self.conn, ROTATION_SETTING, str(index + 1))
        return rotation_slice(ebay_queries(catalog), index, slices)

    def _run_source(
        self,
        name: str,
        source: Source,
        catalog: Catalog,
        pending: dict[int, dict[Reason, list[str]]],
    ) -> tuple[str, int]:
        run_id = db.start_run(self.conn, name, self.clock())
        seen = 0
        try:
            queries = self._queries(name, catalog)
            for query in queries:
                listings = source.search(query)
                seen += len(listings)
                for listing in listings:
                    self._process(listing, catalog, pending)
        except Exception as e:  # noqa: BLE001 - one failing source never stops the others
            log.warning("source %s failed: %s", name, e)
            db.finish_run(self.conn, run_id, "error", seen, f"{type(e).__name__}: {e}",
                          self.clock())
            return "error", seen
        # A source can only report what it parsed, so zero listings across every
        # query of the run stands in for "the site answered but nothing parsed".
        status = "site_change" if queries and seen == 0 else "ok"
        db.finish_run(self.conn, run_id, status, seen, None, self.clock())
        return status, seen

    def _process(
        self, listing: Listing, catalog: Catalog, pending: dict[int, dict[Reason, list[str]]]
    ) -> None:
        c = classify(listing, catalog, self.cfg)
        if c is None:
            return
        now = self.clock()
        if c.kind == "scan_only":
            scan = db.get_scan_by_source(self.conn, listing.source, listing.source_id)
            if scan is not None:
                c = self._lot_from_estimate(c, scan["unit_count_estimate"], catalog) or c
        scored = self._score(listing, c, catalog, now)
        listing_id = db.upsert_listing(self.conn, listing, c, scored, now)
        if scored is not None:
            reasons = self._deal_reasons(c, scored, catalog)
            if reasons:
                pending.setdefault(listing_id, {}).update(reasons)

    def _score(
        self, listing: Listing, c: Classified, catalog: Catalog, now: datetime
    ) -> Scored | None:
        if c.kind == "single":
            product = catalog.product(c.key)
            value = self.market.product_value(product, c.condition)
            return score_single(listing, product, value, self.cfg, now)
        if c.kind == "hardware_lot":
            bulk = catalog.bulk_product(c.key)
            value = self.market.bulk_value(bulk)
            return score_hardware_lot(listing, bulk, c.quantity, value, self.cfg, now)
        if c.kind == "game_lot":
            value = self.market.per_game_value(catalog.platform(c.key))
            return score_game_lot(listing, c.quantity, value, self.cfg, now)
        return None  # scan_only: no quantity to score until a scan estimates one

    def _lot_from_estimate(
        self, c: Classified, estimate: int | None, catalog: Catalog
    ) -> Classified | None:
        """The lot kind a scan_only listing becomes once photos give it a count."""
        if estimate is None:
            return None
        if c.category is None:  # platform key: a game lot
            if estimate >= self.cfg.min_game_lot:
                return Classified(kind="game_lot", key=c.key, condition=None,
                                  quantity=estimate, category=None)
            return None
        bulk = next((b for b in catalog.bulk if b.id == c.key), None)
        if bulk is not None and estimate >= self.cfg.min_hardware_lot:
            return Classified(kind="hardware_lot", key=c.key, condition=None,
                              quantity=estimate, category=bulk.category)
        return None

    def _deal_reasons(
        self, c: Classified, s: Scored, catalog: Catalog
    ) -> dict[Reason, list[str]]:
        reasons: dict[Reason, list[str]] = {}
        if c.kind == "single":
            product = catalog.product(c.key)
            if s.is_target_hit:
                reasons["target_hit"] = [
                    f"Target hit: ${s.total_cost:,.2f} total"
                    f" (max ${product.max_price:,.2f}) for {product.name}"]
            if (s.profit is not None and s.discount_pct is not None
                    and s.profit >= self.cfg.min_profit
                    and s.discount_pct >= self.cfg.min_discount):
                reasons["resale_deal"] = [
                    f"Resale profit ${s.profit:,.2f}, {s.discount_pct:.0%} below market"]
        elif c.kind == "hardware_lot" and s.is_deal:
            bulk = catalog.bulk_product(c.key)
            reasons["hardware_lot_deal"] = [
                f"{c.quantity} × {bulk.name}: expected profit ${s.profit:,.2f}"
                f" (${s.total_cost / c.quantity:,.2f} per unit, repair ${bulk.repair_cost:,.2f},"
                f" {bulk.success_rate:.0%} success)"]
        elif c.kind == "game_lot" and s.is_deal:
            reasons["game_lot_deal"] = [
                f"{c.quantity} games at ${s.total_cost / c.quantity:,.2f} each:"
                f" expected profit ${s.profit:,.2f}, {s.discount_pct:.0%} below market"]
        return reasons

    def _notify(self, listing_id: int, reason: Reason, details: list[str]) -> bool:
        return self.notifier is not None and self.notifier.notify(listing_id, reason, details)

    # --- photo scans --------------------------------------------------------

    def _gate_cost(self, row: sqlite3.Row) -> float:
        """Landed cost for the gate; an unscored (scan_only) row estimates one unit of
        shipping, or the lot base for a game lot."""
        if row["total_cost"] is not None:
            return row["total_cost"]
        est = (self.cfg.buy_shipping_estimate[row["category"]] if row["category"]
               else self.cfg.lot_ship_base)
        return total_cost(_row_listing(row), self.cfg, est)[0]

    def _auto_scan(self, catalog: Catalog) -> tuple[int, int]:
        scanned = notified = 0
        for row in db.scan_candidates(self.conn, self.clock()):
            decision = self.budget.can_auto_scan(self._gate_cost(row))
            if not decision.allowed:
                if decision.reason in _GATE_STOPS:
                    break
                continue  # over max scan price: a cheaper candidate may still pass
            try:
                outcome = self._scan(row["id"], manual=False, catalog=catalog)
            except ScanDeferred as e:
                log.info("photo scans deferred: %s", e)
                break
            except Exception as e:  # noqa: BLE001 - a failed scan never stops a refresh
                log.warning("photo scan of listing %s failed: %s", row["id"], e)
                continue
            if outcome is not None:
                scanned += 1
                notified += outcome[1]
        return scanned, notified

    def scan_listing(self, listing_id: int, manual: bool) -> ScanResult | None:
        if self.scanner is None:
            return None
        catalog = self.catalog_loader()  # loaded before taking the scan lock
        try:
            outcome = self._scan(listing_id, manual, catalog)
        except ScanDeferred as e:
            log.info("photo scan of listing %s deferred: %s", listing_id, e)
            return None
        return outcome[0] if outcome is not None else None

    def _scan(
        self, listing_id: int, manual: bool, catalog: Catalog
    ) -> tuple[ScanResult, bool] | None:
        """Scan one listing; returns the result as saved and whether a photo_find was
        sent, or None when the scan did not run."""
        if self.scanner is None:
            return None
        with self.budget.lock:
            now = self.clock()
            row = db.get_listing(self.conn, listing_id)
            if (row is None or row["kind"] not in SCAN_KINDS or _ended(row, now)
                    or db.get_scan(self.conn, listing_id) is not None):
                return None
            cost = self._gate_cost(row)
            decision = (self.budget.can_manual_scan() if manual
                        else self.budget.can_auto_scan(cost))
            if not decision.allowed:
                return None
            photos = self._photos(row)
            result = self.scanner.scan(photos, row["title"])  # ScanDeferred propagates
            # The scan is paid for: from here on a scans row must be written, whatever
            # fails, or the listing would stay a candidate and be billed again.
            find_eval = None
            try:
                if result.cost_usd > 0:
                    self.budget.record(listing_id, result.input_tokens, result.output_tokens,
                                       result.cost_usd)
                if result.status == "ok":
                    if row["kind"] == "scan_only":
                        cost = self._rescore(row, result.unit_count_estimate, catalog,
                                             now) or cost
                    find_eval = evaluate_finds(
                        result,
                        lambda item: self.market.item_price(item.platform, item.title,
                                                            item.completeness),
                        cost, self.cfg)
                db.save_scan(self.conn, listing_id, result, find_eval, now, photo_urls=photos)
            except Exception as e:  # noqa: BLE001 - record the paid scan as failed instead
                log.exception("processing the scan of listing %s failed", listing_id)
                find_eval = None
                result = self._save_failed_scan(listing_id, result, e, now, photos)
        sent = False
        if find_eval is not None and (find_eval.is_rare_find or find_eval.is_lot_find):
            sent = self._notify(listing_id, "photo_find", self._find_details(find_eval, cost))
        return result, sent

    def _photos(self, row: sqlite3.Row) -> list[str]:
        stored = json.loads(row["image_urls"] or "[]")
        source = self.sources.get(row["source"])
        if source is None:
            return stored
        try:
            return source.photos(row["source_id"], stored)
        except Exception as e:  # noqa: BLE001 - fall back to the listing's own photos
            log.warning("photos for listing %s failed: %s", row["id"], e)
            return stored

    def _save_failed_scan(
        self, listing_id: int, result: ScanResult, error: Exception, now: datetime,
        photos: list[str],
    ) -> ScanResult:
        """Store a scan whose post-processing failed; a second failure retries once
        with a minimal row, and a third raises."""
        self.conn.rollback()
        message = f"processing the scan failed: {type(error).__name__}: {error}"
        failed = result if result.status != "ok" else replace(
            result, status="failed", error=message)
        try:
            db.save_scan(self.conn, listing_id, failed, None, now, photo_urls=photos)
            return failed
        except Exception:  # noqa: BLE001 - fall back to a row with nothing to serialize
            log.exception("saving the failed scan of listing %s failed", listing_id)
            self.conn.rollback()
        minimal = ScanResult(
            status="failed", items=[], unit_count_estimate=None, notes="",
            model=result.model, input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, cost_usd=result.cost_usd, error=message)
        db.save_scan(self.conn, listing_id, minimal, None, now, photo_urls=[])
        return minimal

    def _rescore(
        self, row: sqlite3.Row, estimate: int | None, catalog: Catalog, now: datetime
    ) -> float | None:
        """Re-score a scan_only row as a lot from the scan's unit count; its total cost."""
        c = self._lot_from_estimate(_row_classified(row), estimate, catalog)
        if c is None:
            return None
        listing = _row_listing(row)
        scored = self._score(listing, c, catalog, now)
        db.upsert_listing(self.conn, listing, c, scored, now)
        return scored.total_cost if scored is not None else None

    @staticmethod
    def _find_details(ev: FindEval, cost: float) -> list[str]:
        details = []
        for item, value in ev.rare_items:
            name = f"{item.title} {item.edition}" if item.edition else item.title
            qty = f" ×{item.quantity}" if item.quantity > 1 else ""
            details.append(
                f"{name} ({item.platform}, {item.completeness}){qty} — {_money(value)}")
        if ev.is_lot_find:
            details.append(
                f"Identified value {_money(ev.identified_value)} vs total cost ${cost:,.2f}")
        return details
