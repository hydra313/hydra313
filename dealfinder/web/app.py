"""The dashboard app: one long-lived refresh pipeline, the scheduler and manual scans (§14)."""
from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dealfinder import db
from dealfinder.budget import Budget
from dealfinder.catalog import Catalog, load_catalog
from dealfinder.config import Config
from dealfinder.market import MarketService
from dealfinder.notify import Notifier
from dealfinder.pipeline import Pipeline, SpendNotRecorded
from dealfinder.sources.base import HttpClient, Source
from dealfinder.sources.ebay import EbaySource
from dealfinder.sources.ebay_sold import SoldFetcher
from dealfinder.sources.hibid import HiBidSource
from dealfinder.sources.shopgoodwill import ShopGoodwillSource
from dealfinder.vision import VisionScanner

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent
NOTIFY_TIMEOUT_S = 20.0

PipelineFactory = Callable[[sqlite3.Connection], Pipeline]


def money(value: float) -> str:
    return f"${value:,.2f}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    """Aware local time: the caps' day and month boundaries are local (§11.2)."""
    return datetime.now().astimezone()


class _LiveCatalog:
    """The most recently loaded catalog, so a long-lived MarketService sees catalog edits."""

    def __init__(self, catalog: Catalog) -> None:
        self.current = catalog

    def __getattr__(self, name: str):
        return getattr(self.current, name)


def default_pipeline_factory(cfg: Config) -> PipelineFactory:
    """Real sources and services; eBay, photo scanning and Discord only when configured."""

    def build(conn: sqlite3.Connection) -> Pipeline:
        http = HttpClient()
        catalog = _LiveCatalog(load_catalog(cfg.catalog_dir, conn, cfg))

        def load() -> Catalog:
            catalog.current = load_catalog(cfg.catalog_dir, conn, cfg)
            return catalog.current

        ebay = None
        if cfg.ebay_client_id and cfg.ebay_client_secret:
            ebay = EbaySource(http, cfg.ebay_client_id, cfg.ebay_client_secret, utc_now)
        sources: dict[str, Source] = {}
        if ebay is not None:
            sources["ebay"] = ebay
        sources["shopgoodwill"] = ShopGoodwillSource(http)
        sources["hibid"] = HiBidSource(http, utc_now)
        market = MarketService(conn, cfg, SoldFetcher(http), ebay, catalog, utc_now)
        scanner = None
        if cfg.anthropic_api_key:
            client = anthropic.Anthropic(api_key=cfg.anthropic_api_key, max_retries=1)
            scanner = VisionScanner(client, http, cfg)
        notifier = None
        if cfg.discord_webhook_url:
            notifier = Notifier(conn, cfg, httpx.Client(timeout=NOTIFY_TIMEOUT_S))
        return Pipeline(conn, cfg, load, sources, market, scanner,
                        Budget(conn, cfg, local_now), notifier, utc_now)

    return build


class Runner:
    """Runs refreshes of the one long-lived pipeline, never two at once, and manual scans
    on short-lived pipelines with their own connections."""

    def __init__(self, cfg: Config, build_pipeline: PipelineFactory) -> None:
        self.cfg = cfg
        self.build_pipeline = build_pipeline
        self.conn = db.connect(cfg.db_path)
        self.pipeline = build_pipeline(self.conn)
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start_refresh(self) -> bool:
        """Start a refresh in the background; False when one is already running."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            self.thread = threading.Thread(target=self._refresh_then_release,
                                           name="refresh", daemon=True)
            self.thread.start()
        except BaseException:
            self.lock.release()
            raise
        return True

    def scheduled_refresh(self) -> None:
        """The scheduler's job: skipped while paused or while a refresh is running."""
        if not self.lock.acquire(blocking=False):
            log.info("scheduled refresh skipped: a refresh is already running")
            return
        try:
            if Budget(self.conn, self.cfg, local_now).paused():
                log.info("scheduled refresh skipped: the scheduler is paused")
                return
            self._refresh()
        finally:
            self.lock.release()

    def _refresh_then_release(self) -> None:
        try:
            self._refresh()
        finally:
            self.lock.release()

    def _refresh(self) -> None:
        try:
            report = self.pipeline.refresh()
        except Exception:  # noqa: BLE001 - a failed refresh must not kill the scheduler
            log.exception("refresh failed")
            return
        log.info("refresh done: %s, %d scanned, %d notified",
                 report.per_source, report.scanned, report.notified)

    def start_scan(self, listing_id: int) -> threading.Thread:
        """Scan one listing manually in the background."""
        thread = threading.Thread(target=self._scan, args=(listing_id,),
                                  name=f"scan-{listing_id}", daemon=True)
        thread.start()
        return thread

    def _scan(self, listing_id: int) -> None:
        conn = None
        try:
            conn = db.connect(self.cfg.db_path)
            self.build_pipeline(conn).scan_listing(listing_id, manual=True)
        except SpendNotRecorded as e:
            log.error("manual scan of listing %s: %s", listing_id, e)
        except Exception:  # noqa: BLE001 - a background scan has no caller to raise to
            log.exception("manual scan of listing %s failed", listing_id)
        finally:
            if conn is not None:
                conn.close()

    def close(self) -> None:
        """Close the refresh connection unless a refresh still holds it."""
        if self.lock.acquire(blocking=False):  # kept held: no refresh starts afterwards
            self.conn.close()


def create_app(
    cfg: Config,
    *,
    build_pipeline: PipelineFactory | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    from dealfinder.web.routes import router

    runner = Runner(cfg, build_pipeline or default_pipeline_factory(cfg))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if start_scheduler:
            scheduler = BackgroundScheduler()
            scheduler.add_job(runner.scheduled_refresh, "interval",
                              minutes=cfg.refresh_minutes, max_instances=1, coalesce=True)
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            runner.close()

    app = FastAPI(title="dealfinder", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.runner = runner
    app.state.templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.state.templates.env.filters["money"] = money
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(router)
    return app
