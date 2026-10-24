from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

import httpx

from dealfinder import db
from dealfinder.config import Config

log = logging.getLogger(__name__)

Reason = Literal["photo_find", "target_hit", "hardware_lot_deal", "game_lot_deal", "resale_deal"]

MIN_SPACING_S = 1.0
MAX_RETRY_AFTER_S = 30.0  # a longer Discord wait is not worth stalling a refresh for
_TITLE_MAX = 256
_DESCRIPTION_MAX = 4000  # Discord allows 4096
_SITE_NAMES = {"ebay": "eBay", "shopgoodwill": "ShopGoodwill", "hibid": "HiBid"}


class _RedactWebhook(logging.Filter):
    """httpx logs every request URL at INFO; the webhook URL is a secret."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            message = record.getMessage()
            if any(secret in message for secret in self.secrets):
                for secret in self.secrets:
                    message = message.replace(secret, "<discord webhook>")
                record.msg, record.args = message, None
        return True


_redactor = _RedactWebhook()
logging.getLogger("httpx").addFilter(_redactor)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _time_left(row: sqlite3.Row, now: datetime) -> str:
    if not row["is_auction"] or not row["end_time"]:
        return "Buy It Now"
    seconds = (datetime.fromisoformat(row["end_time"]) - now).total_seconds()
    minutes = max(int(seconds // 60), 0)
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m" if mins else "<1m"


def _first_image(row: sqlite3.Row) -> str | None:
    try:
        urls = json.loads(row["image_urls"] or "[]")
    except ValueError:
        return None
    return urls[0] if urls else None


def build_payload(row: sqlite3.Row, reason: Reason, details: list[str], now: datetime) -> dict:
    total = row["total_cost"]
    embed: dict = {
        "title": _truncate(row["title"], _TITLE_MAX),
        "url": row["url"],
        "description": _truncate("\n".join(details), _DESCRIPTION_MAX),
        "fields": [
            {
                "name": "Total cost",
                "value": f"${total:,.2f}" if total is not None else "Unknown",
                "inline": True,
            },
            {"name": "Time left", "value": _time_left(row, now), "inline": True},
            {
                "name": "Site",
                "value": _SITE_NAMES.get(row["source"], row["source"]),
                "inline": True,
            },
        ],
    }
    image = _first_image(row)
    if image:
        embed["thumbnail"] = {"url": image}
    return {"embeds": [embed]}


def _retry_after(resp: httpx.Response) -> float | None:
    """Seconds to wait before the retry; None when the wait is too long or not a number."""
    try:
        value = float(resp.json()["retry_after"])
    except (ValueError, KeyError, TypeError):
        return MIN_SPACING_S
    if not math.isfinite(value) or value > MAX_RETRY_AFTER_S:
        return None
    return value if value > 0 else MIN_SPACING_S


class Notifier:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cfg: Config,
        http: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.conn = conn
        self.cfg = cfg
        self.http = http
        self.sleep = sleep
        self.clock = clock
        self._last_send: datetime | None = None
        if cfg.discord_webhook_url:
            _redactor.secrets.add(cfg.discord_webhook_url)

    def enabled(self) -> bool:
        if self.cfg.discord_webhook_url:
            _redactor.secrets.add(self.cfg.discord_webhook_url)
        return bool(self.cfg.discord_webhook_url)

    def notify(self, listing_id: int, reason: Reason, details: list[str]) -> bool:
        if not self.cfg.notify_reasons.get(reason, False) or not self.enabled():
            return False
        row = db.get_listing(self.conn, listing_id)
        if row is None:
            return False
        now = self.clock()
        if row["end_time"] and datetime.fromisoformat(row["end_time"]) < now:
            return False
        if not db.claim_notification(self.conn, listing_id, reason, now):
            return False
        try:
            ok = self._deliver(build_payload(row, reason, details, now))
        except Exception as e:  # noqa: BLE001 - once claimed, nothing may stop a refresh
            log.warning("discord notification error: %s", type(e).__name__)
            ok = False
        db.mark_notification(self.conn, listing_id, reason, "sent" if ok else "failed")
        if not ok:
            log.warning("discord notification for listing %s (%s) failed", listing_id, reason)
        return ok

    def _deliver(self, payload: dict) -> bool:
        """Post to the webhook; never raises. Errors are reported by type/status only,
        since exception text and URLs could expose the webhook secret."""
        if self._last_send is not None:
            wait = MIN_SPACING_S - (self.clock() - self._last_send).total_seconds()
            if wait > 0:
                self.sleep(wait)
        resp = self._post(payload)
        if resp is not None and resp.status_code == 429:
            wait = _retry_after(resp)
            if wait is None:
                log.warning("discord rate limit wait too long; giving up")
                return False
            self.sleep(wait)
            resp = self._post(payload)
        return resp is not None and resp.is_success

    def _post(self, payload: dict) -> httpx.Response | None:
        url = self.cfg.discord_webhook_url
        if not url:
            return None
        _redactor.secrets.add(url)
        try:
            resp = self.http.post(url, json=payload)
        except Exception as e:  # noqa: BLE001 - a failed send must never stop a refresh
            log.warning("discord webhook request failed: %s", type(e).__name__)
            resp = None
        else:
            if not resp.is_success:
                log.warning("discord webhook returned HTTP %s", resp.status_code)
        self._last_send = self.clock()
        return resp
