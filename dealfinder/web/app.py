"""The dashboard app: one long-lived refresh pipeline, the scheduler and manual scans (§14)."""
from __future__ import annotations

import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dealfinder import db
from dealfinder.budget import Budget
from dealfinder.catalog import Catalog, load_catalog
from dealfinder.config import Config
from dealfinder.connections import load_saved, load_preferences
from dealfinder.market import MarketService
from dealfinder.notify import Notifier
from dealfinder.pipeline import Pipeline, SpendNotRecorded
from dealfinder.sources.base import HttpClient, Source
from dealfinder.sources.ebay import EbaySource
from dealfinder.sources.ebay_browser import EbayBrowserSource
from dealfinder.sources.ebay_sold import SoldFetcher
from dealfinder.sources.ebay_sold_browser import BrowserSoldFetcher
from dealfinder.sources.hibid import HiBidSource
from dealfinder.sources.hibid_browser import HiBidBrowserSource
from dealfinder.sources.shopgoodwill import ShopGoodwillSource
from dealfinder.vision import VisionScanner

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent
NOTIFY_TIMEOUT_S = 20.0

PipelineFactory = Callable[[sqlite3.Connection], Pipeline]

LOCAL_HOSTS = ("127.0.0.1", "localhost")
SAFE_METHODS = ("GET", "HEAD")


def _local_port(host: str) -> str | None:
    """The port of a "127.0.0.1:<port>" or "localhost:<port>" Host header, else None."""
    name, _, port = host.rpartition(":")
    return port if name in LOCAL_HOSTS and port.isdigit() else None


def request_allowed(method: str, headers) -> bool:
    """CSRF and DNS-rebinding guard: a state-changing request must come from htmx
    (a custom header forces a CORS preflight, which the app never grants), be sent to
    a local Host, and carry a local Origin on the same port when it has one."""
    if method in SAFE_METHODS:
        return True
    if headers.get("hx-request") != "true":
        return False
    port = _local_port(headers.get("host", ""))
    if port is None:
        return False
    origin = headers.get("origin")
    return origin is None or origin in {f"http://{h}:{port}" for h in LOCAL_HOSTS}


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


class DefaultPipelineFactory:
    """Real sources and services; eBay, photo scanning and Discord only when configured.

    Every pipeline it builds (the refresh pipeline and each manual scan's) shares one
    HttpClient, so per-host throttling holds across threads, and one Discord client.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.http = HttpClient()
        self.notify_http = httpx.Client(timeout=NOTIFY_TIMEOUT_S)

    def __call__(self, conn: sqlite3.Connection) -> Pipeline:
        cfg, http = self.cfg, self.http
        catalog = _LiveCatalog(load_catalog(cfg.catalog_dir, conn, cfg))

        def load() -> Catalog:
            catalog.current = load_catalog(cfg.catalog_dir, conn, cfg)
            return catalog.current

        ebay = None
        if cfg.ebay_client_id and cfg.ebay_client_secret:
            ebay = EbaySource(http, cfg.ebay_client_id, cfg.ebay_client_secret, utc_now)
        sources: dict[str, Source] = {"ebay": ebay or EbayBrowserSource(conn, cfg, utc_now)}
        sources["shopgoodwill"] = ShopGoodwillSource(http)
        if cfg.hibid_mode == "browser":
            sources["hibid"] = HiBidBrowserSource(conn, cfg, utc_now)
        elif cfg.hibid_mode == "api":
            sources["hibid"] = HiBidSource(http, utc_now)
        market = MarketService(conn, cfg, BrowserSoldFetcher(http, conn, cfg, utc_now), ebay, catalog, utc_now)
        scanner = None
        # Paid photo scanning was removed from the shipped app.
        notifier = Notifier(conn, cfg, self.notify_http)
        return Pipeline(conn, cfg, load, sources, market, scanner,
                        Budget(conn, cfg, local_now), notifier, utc_now)

    def close(self) -> None:
        self.http.close()
        if self.notify_http is not None:
            self.notify_http.close()


def default_pipeline_factory(cfg: Config) -> DefaultPipelineFactory:
    return DefaultPipelineFactory(cfg)


class Runner:
    """Runs refreshes of the one long-lived pipeline, never two at once, and manual scans
    on short-lived pipelines with their own connections."""

    def __init__(self, cfg: Config, build_pipeline: PipelineFactory) -> None:
        self.cfg = cfg
        self.build_pipeline = build_pipeline
        self.conn = db.connect(cfg.db_path)
        self.pipeline = build_pipeline(self.conn)
        self.progress: dict = {}
        self.last_report = None
        self.refresh_error: str | None = None
        self.finished_at: datetime | None = None
        self.pipeline.progress = self._progress
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        # The last manual scan's error and its listing, shown until a manual scan succeeds.
        self.scan_error: tuple[int, str] | None = None

    @property
    def refresh_running(self) -> bool:
        return self.lock.locked()

    def _progress(self, event: dict) -> None:
        self.progress = {**self.progress, **event}

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
        self.progress = {"stage": "Starting", "started_at": utc_now()}
        self.refresh_error = None
        try:
            report = self.pipeline.refresh()
        except Exception as error:  # noqa: BLE001 - a failed refresh must not kill the scheduler
            log.exception("refresh failed")
            self.refresh_error = f"{type(error).__name__}: {error}"[:200]
            self.finished_at = utc_now()
            return
        self.last_report = report
        self.finished_at = utc_now()
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
            result = self.build_pipeline(conn).scan_listing(listing_id, manual=True)
            if result is not None and result.status == "ok":
                self.scan_error = None
        except SpendNotRecorded as e:
            log.error("manual scan of listing %s: %s", listing_id, e)
            self.scan_error = (listing_id, "Manual photo scan: spend not recorded"
                                           " — caps may be off by one scan")
        except Exception as e:  # noqa: BLE001 - a background scan has no caller to raise to
            log.exception("manual scan of listing %s failed", listing_id)
            self.scan_error = (listing_id,
                               f"Manual photo scan failed: {type(e).__name__}: {e}")
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

    try:
        load_saved(cfg)
    except (ValueError,OSError):
        log.warning('Saved connection could not be opened; reconnect on the Connections page.')
    load_preferences(cfg)
    factory = build_pipeline or default_pipeline_factory(cfg)
    runner = Runner(cfg, factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if start_scheduler:
            scheduler = BackgroundScheduler()
            # First run right away: an unattended alert app should not wait an interval.
            scheduler.add_job(runner.scheduled_refresh, "interval",
                              minutes=cfg.refresh_minutes, max_instances=1, coalesce=True,
                              next_run_time=datetime.now())
            scheduler.start()
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            runner.close()
            close = getattr(factory, "close", None)
            if callable(close):
                close()

    app = FastAPI(title="ConsoleHawk", lifespan=lifespan)
    # Local capability token: only the narrowly scoped helper endpoints accept it.
    # Keep it out of SQLite, source archives, logs, and external requests.
    token_path = Path(cfg.db_path).with_suffix(".browser-token")
    helper_token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    if len(helper_token) < 40:
        helper_token = secrets.token_urlsafe(32)
        token_path.write_text(helper_token, encoding="utf-8")
    app.state.browser_token = helper_token
    app.state.browser_lock = threading.Lock()

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.url.path in {"/browser/next", "/browser/ebay/next", "/browser/ebay/results",
                                "/browser/hibid/next", "/browser/hibid/results"}:
            token = request.headers.get("x-dealfinder-token", "")
            if (_local_port(request.headers.get("host", "")) is None
                    or not secrets.compare_digest(token, app.state.browser_token)):
                return PlainTextResponse("Forbidden", status_code=403)
            return await call_next(request)
        if not request_allowed(request.method, request.headers):
            return PlainTextResponse("Forbidden", status_code=403)
        return await call_next(request)

    app.state.cfg = cfg
    app.state.runner = runner
    app.state.templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.state.templates.env.filters["money"] = money
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(router)
    return app
