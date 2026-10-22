from __future__ import annotations

import math
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from dealfinder import db
from dealfinder.config import Config

# One lock for every scan path in the process, whichever Budget instance
# (one per DB connection/thread) is asking.
SCAN_LOCK = threading.Lock()


@dataclass(frozen=True)
class Caps:
    daily: float
    monthly: float
    max_scan_price: float


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


class Budget:
    """Photo-scan gate (§11.2) and spend tracking.

    `clock` returns an aware datetime in the machine's local zone; day and month
    boundaries are taken in that zone and converted to UTC for queries.
    """

    def __init__(
        self, conn: sqlite3.Connection, cfg: Config, clock: Callable[[], datetime]
    ) -> None:
        self.conn = conn
        self.cfg = cfg
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must return an aware datetime")
        return now

    @property
    def lock(self) -> threading.Lock:
        return SCAN_LOCK

    # --- switches ---------------------------------------------------------

    def _flag(self, key: str) -> bool:
        return db.get_setting(self.conn, key, "false") == "true"

    def _set_flag(self, key: str, on: bool) -> None:
        db.set_setting(self.conn, key, "true" if on else "false")

    def scan_enabled(self) -> bool:
        return self._flag("scan_enabled")

    def set_scan_enabled(self, on: bool) -> None:
        self._set_flag("scan_enabled", on)

    def paused(self) -> bool:
        return self._flag("scheduler_paused")

    def set_paused(self, on: bool) -> None:
        self._set_flag("scheduler_paused", on)

    # --- caps -------------------------------------------------------------

    def _number(self, key: str, default: float) -> float:
        """A stored cap; absent, unparseable, non-finite or negative values yield `default`.

        Falling back to the configured default (rather than trusting a bad value) keeps
        the gate closed: `spent > nan` is never true, so a nan cap would allow every scan.
        """
        value = db.get_setting(self.conn, key)
        if value is None:
            return default
        try:
            number = float(value)
        except ValueError:
            return default
        return number if math.isfinite(number) and number >= 0 else default

    def caps(self) -> Caps:
        return Caps(
            daily=self._number("daily_cap", self.cfg.initial_daily_cap),
            monthly=self._number("monthly_cap", self.cfg.initial_monthly_cap),
            max_scan_price=self._number("max_scan_price", self.cfg.initial_max_scan_price),
        )

    def set_caps(
        self,
        daily: float | None = None,
        monthly: float | None = None,
        max_scan_price: float | None = None,
    ) -> None:
        """Store the given caps; ValueError (nothing written) if any is negative or not finite."""
        given = {
            "daily_cap": daily,
            "monthly_cap": monthly,
            "max_scan_price": max_scan_price,
        }
        numbers: dict[str, float] = {}
        for key, value in given.items():
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number, got {value!r}") from None
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"{key} must be a finite number >= 0, got {value!r}")
            numbers[key] = number
        for key, number in numbers.items():
            db.set_setting(self.conn, key, repr(number))

    # --- spend ------------------------------------------------------------

    def spent_today(self) -> float:
        now = self._now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return db.spend_between(self.conn, start, start + timedelta(days=1))

    def spent_this_month(self) -> float:
        now = self._now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return db.spend_between(self.conn, start, end)

    def record(
        self, listing_id: int, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        if not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError(f"cost_usd must be a finite number >= 0, got {cost_usd!r}")
        db.record_spend(
            self.conn, listing_id, input_tokens, output_tokens, cost_usd, self._now()
        )

    # --- gate -------------------------------------------------------------

    def _cap_check(self) -> GateDecision | None:
        caps = self.caps()
        ceiling = self.cfg.scan_cost_ceiling
        if self.spent_today() + ceiling > caps.daily:
            return GateDecision(False, "daily cap reached")
        if self.spent_this_month() + ceiling > caps.monthly:
            return GateDecision(False, "monthly cap reached")
        return None

    def can_auto_scan(self, total_cost: float) -> GateDecision:
        if not self.scan_enabled():
            return GateDecision(False, "scanning is off")
        if total_cost > self.caps().max_scan_price:
            return GateDecision(False, "over max scan price")
        return self._cap_check() or GateDecision(True, "ok")

    def can_manual_scan(self) -> GateDecision:
        return self._cap_check() or GateDecision(True, "ok")
