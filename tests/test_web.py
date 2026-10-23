import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dealfinder import db
from dealfinder.budget import Budget
from dealfinder.config import Config
from dealfinder.models import Classified, Listing, ScanResult, Scored
from dealfinder.pipeline import RefreshReport, SpendNotRecorded
from dealfinder.web.app import create_app, default_pipeline_factory

CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"


def now_utc():
    return datetime.now(timezone.utc)


def local_now():
    return datetime.now().astimezone()


class FakePipeline:
    def __init__(self):
        self.calls = 0
        self.block = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.scan_error: Exception | None = None
        self.scanned: list[tuple[int, bool]] = []

    def refresh(self):
        self.calls += 1
        self.started.set()
        if self.block:
            self.release.wait(5)
        return RefreshReport()

    def scan_listing(self, listing_id, manual):
        self.scanned.append((listing_id, manual))
        if self.scan_error is not None:
            raise self.scan_error
        return None


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=str(tmp_path / "test.db"), catalog_dir=CATALOG_DIR)


@pytest.fixture
def fake():
    return FakePipeline()


@pytest.fixture
def client(cfg, fake):
    app = create_app(cfg, build_pipeline=lambda conn: fake, start_scheduler=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    yield c
    c.close()


def seed(conn, source_id, *, source="ebay", key="rtx-4090", category="gpu",
         condition="used", title=None, price=500.0, end_in_h=None, first_seen=None,
         target=False, deal=True, profit=120.0):
    listing = Listing(
        source=source, source_id=source_id, title=title or f"Listing {source_id}",
        url=f"https://example.com/{source_id}", price=price, shipping=20.0,
        shipping_offered=True, image_urls=[f"https://img.example.com/{source_id}.jpg"],
        is_auction=end_in_h is not None,
        end_time=now_utc() + timedelta(hours=end_in_h) if end_in_h is not None else None)
    c = Classified(kind="single", key=key, condition=condition, quantity=1, category=category)
    s = Scored(total_cost=price + 20.0, shipping_estimated=False, profit=profit,
               discount_pct=0.3, is_target_hit=target, is_deal=deal or target,
               rank_score=profit)
    return db.upsert_listing(conn, listing, c, s, first_seen or now_utc() - timedelta(hours=1))


def row_html(html, listing_id):
    m = re.search(rf'<tr id="listing-{listing_id}".*?</tr>', html, re.DOTALL)
    return m.group(0) if m else None


# --- deals page ----------------------------------------------------------------


def test_deals_page_lists_deals(client, conn):
    plain = seed(conn, "a1", title="RTX 4090 Founders")
    hit = seed(conn, "a2", title="RTX 4090 cheap", target=True)
    gone = seed(conn, "a3", title="RTX 4090 dismissed")
    no_deal = seed(conn, "a4", title="RTX 4090 overpriced", deal=False)
    db.dismiss_listing(conn, gone)

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "https://unpkg.com/htmx.org@2.0.4" in html
    assert row_html(html, plain) is not None
    assert "RTX 4090 Founders" in html
    assert "GeForce RTX 4090" in html  # product name from the catalog
    assert "🎯" in row_html(html, hit)
    assert "🎯" not in row_html(html, plain)
    assert row_html(html, gone) is None
    assert row_html(html, no_deal) is None
    assert "$520.00" in row_html(html, plain)  # total cost


def test_filters_query_params(client, conn):
    gpu = seed(conn, "f1", source="ebay", end_in_h=30)
    console = seed(conn, "f2", source="hibid", key="ps5-spider-man-2", category="console",
                   end_in_h=2)
    target = seed(conn, "f3", source="shopgoodwill", target=True, end_in_h=50)

    def ids(params):
        html = client.get("/", params=params).text
        return {i for i in (gpu, console, target) if row_html(html, i) is not None}

    assert ids({}) == {gpu, console, target}
    assert 'hx-push-url="true"' in client.get("/").text  # filters survive a reload
    assert ids({"category": "console"}) == {console}
    assert ids({"source": "hibid"}) == {console}
    assert ids({"ending_within_h": "3"}) == {console}
    assert ids({"target_only": "true"}) == {target}
    assert ids({"category": "", "source": "", "ending_within_h": ""}) == {gpu, console, target}
    # An HTMX filter request gets only the rows.
    partial = client.get("/", params={"category": "gpu"}, headers={"HX-Request": "true"})
    assert "<html" not in partial.text
    assert row_html(partial.text, gpu) is not None


def test_new_badge_uses_previous_visit(client, conn):
    previous = now_utc() - timedelta(hours=2)
    db.set_setting(conn, "last_visit", previous.isoformat())
    old = seed(conn, "n1", first_seen=previous - timedelta(hours=1))
    new = seed(conn, "n2", first_seen=previous + timedelta(hours=1))

    html = client.get("/").text
    assert "NEW" in row_html(html, new)
    assert "NEW" not in row_html(html, old)
    stored = datetime.fromisoformat(db.get_setting(conn, "last_visit"))
    assert stored > previous + timedelta(hours=1)

    # HTMX filter requests keep the badges of the page they belong to.
    partial = client.get("/", params={"new_only": "true"}, headers={"HX-Request": "true"})
    assert row_html(partial.text, new) is not None
    assert row_html(partial.text, old) is None
    assert "NEW" in row_html(partial.text, new)

    # The next full page view compares against the visit just recorded.
    html = client.get("/").text
    assert "NEW" not in row_html(html, new)


# --- header controls -----------------------------------------------------------


def test_toggle_scan_persists(client, cfg, conn):
    assert "Photo scanning: off" in client.get("/").text  # off on first run (§11.2)

    resp = client.post("/controls/scan", data={"on": "true"})
    assert resp.status_code == 200
    assert "Photo scanning: on" in resp.text
    assert Budget(conn, cfg, local_now).scan_enabled() is True

    resp = client.post("/controls/scan", data={"on": "false"})
    assert "Photo scanning: off" in resp.text
    assert Budget(conn, cfg, local_now).scan_enabled() is False


def test_caps_validation(client, cfg, conn):
    resp = client.post("/controls/caps",
                       data={"daily": "-1", "monthly": "40", "max_scan_price": "200"})
    assert resp.status_code == 400
    assert "must be a finite number" in resp.text
    assert Budget(conn, cfg, local_now).caps().monthly == 50.0  # nothing written

    resp = client.post("/controls/caps",
                       data={"daily": "abc", "monthly": "40", "max_scan_price": "200"})
    assert resp.status_code == 400
    assert "must be a number" in resp.text

    resp = client.post("/controls/caps",
                       data={"daily": "3", "monthly": "40", "max_scan_price": "200"})
    assert resp.status_code == 200
    caps = Budget(conn, cfg, local_now).caps()
    assert (caps.daily, caps.monthly, caps.max_scan_price) == (3.0, 40.0, 200.0)
    assert 'value="3.00"' in resp.text


def test_spend_shown_in_header(client, cfg, conn):
    lid = seed(conn, "s1")
    Budget(conn, cfg, local_now).record(lid, 1000, 500, 0.126)
    html = client.get("/").text
    assert "spent today $0.13 / this month $0.13" in html


def test_pause_toggle(client, cfg, conn):
    resp = client.post("/controls/pause", data={"on": "true"})
    assert resp.status_code == 200
    assert "Scheduler: paused" in resp.text
    assert Budget(conn, cfg, local_now).paused() is True

    resp = client.post("/controls/pause", data={"on": "false"})
    assert "Scheduler: running" in resp.text
    assert Budget(conn, cfg, local_now).paused() is False


def test_scheduled_refresh_skips_when_paused(cfg, fake, conn):
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with TestClient(app):
        runner = app.state.runner
        Budget(conn, cfg, local_now).set_paused(True)
        runner.scheduled_refresh()
        assert fake.calls == 0
        Budget(conn, cfg, local_now).set_paused(False)
        runner.scheduled_refresh()
        assert fake.calls == 1


def test_refresh_runs_pipeline_once_when_double_clicked(cfg, fake):
    fake.block = True
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with TestClient(app) as client:
        runner = app.state.runner
        first = client.post("/refresh")
        assert first.status_code == 200 and "Refreshing" in first.text
        assert fake.started.wait(5)
        second = client.post("/refresh")
        assert "Refresh already running" in second.text
        runner.scheduled_refresh()  # the scheduler does not overlap either
        fake.release.set()
        runner.thread.join(5)
        assert fake.calls == 1

        fake.block = False
        assert "Refreshing" in client.post("/refresh").text
        runner.thread.join(5)
        assert fake.calls == 2


def test_scheduler_refreshes_at_startup_then_every_interval(cfg, fake):
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=True)
    with TestClient(app):
        assert fake.started.wait(5)  # the first refresh runs right after startup
        jobs = app.state.scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].trigger.interval == timedelta(minutes=cfg.refresh_minutes)
    assert not app.state.scheduler.running


def test_header_polls_only_while_refreshing(cfg, fake):
    fake.block = True
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with TestClient(app) as client:
        assert 'hx-trigger="every 3s"' not in client.get("/header").text
        started = client.post("/refresh").text
        assert 'hx-trigger="every 3s"' in started and 'id="header-controls"' in started
        assert fake.started.wait(5)
        polled = client.get("/header").text
        assert 'hx-trigger="every 3s"' in polled and "Refreshing" in polled
        assert 'hx-trigger="every 3s"' in client.get("/").text
        fake.release.set()
        app.state.runner.thread.join(5)
        assert 'hx-trigger="every 3s"' not in client.get("/header").text


def test_shutdown_closes_shared_clients(cfg):
    cfg.discord_webhook_url = "https://discord.com/api/webhooks/1/TOKEN"
    factory = default_pipeline_factory(cfg)
    app = create_app(cfg, build_pipeline=factory, start_scheduler=False)
    with TestClient(app):
        refresh_pipeline = app.state.runner.pipeline
    scan_conn = db.connect(cfg.db_path)
    scan_pipeline = factory(scan_conn)
    scan_conn.close()
    # Every pipeline shares one HttpClient and one notifier client; both are closed.
    assert scan_pipeline.market._sold._http is refresh_pipeline.market._sold._http
    assert scan_pipeline.notifier.http is refresh_pipeline.notifier.http
    assert refresh_pipeline.market._sold._http._client.is_closed
    assert refresh_pipeline.notifier.http.is_closed


def test_refresh_pipeline_built_once_on_its_own_connection(cfg, fake):
    built: list[sqlite3.Connection] = []

    def factory(c):
        built.append(c)
        return fake

    app = create_app(cfg, build_pipeline=factory, start_scheduler=False)
    with TestClient(app) as client:
        client.post("/refresh")
        app.state.runner.thread.join(5)
        client.post("/refresh")
        app.state.runner.thread.join(5)
    assert len(built) == 1 and fake.calls == 2


# --- banner --------------------------------------------------------------------


def test_banner_on_site_change(client, conn):
    run = db.start_run(conn, "hibid", now_utc())
    db.finish_run(conn, run, "site_change", 0, None, now_utc())
    run = db.start_run(conn, "shopgoodwill", now_utc())
    db.finish_run(conn, run, "error", 0, "SourceBlocked: 429", now_utc())
    run = db.start_run(conn, "ebay", now_utc())
    db.finish_run(conn, run, "ok", 12, None, now_utc())
    lid = seed(conn, "b1")
    db.claim_notification(conn, lid, "target_hit", now_utc())
    db.mark_notification(conn, lid, "target_hit", "failed")

    html = client.get("/").text
    banner = re.search(r'<div class="banner".*?</div>', html, re.DOTALL).group(0)
    assert "HiBid" in banner and "site change" in banner
    assert "ShopGoodwill" in banner and "SourceBlocked: 429" in banner
    assert "eBay: " not in banner  # its latest run was ok
    assert "1 Discord notification failed in the last 24 hours" in banner
    # last successful run per source
    assert re.search(r"eBay</span>\s*<span[^>]*>just now", html)
    assert re.search(r"HiBid</span>\s*<span[^>]*>never", html)


def test_banner_missing_keys(cfg, fake):
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with TestClient(app) as client:
        banner = client.get("/").text
    assert "EBAY_CLIENT_ID" in banner and "EBAY_CLIENT_SECRET" in banner
    assert "ANTHROPIC_API_KEY" in banner
    assert "DISCORD_WEBHOOK_URL" in banner

    cfg.ebay_client_id = "id-secret-value"
    cfg.ebay_client_secret = "ebay-secret-value"
    cfg.anthropic_api_key = "sk-ant-secret-value"
    cfg.discord_webhook_url = "https://discord.com/api/webhooks/1/hook-secret-value"
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with TestClient(app) as client:
        html = client.get("/").text
    assert "EBAY_CLIENT_ID" not in html
    assert "ANTHROPIC_API_KEY" not in html
    assert "DISCORD_WEBHOOK_URL" not in html
    assert "secret-value" not in html


# --- listing actions -----------------------------------------------------------


def test_dismiss(client, conn):
    lid = seed(conn, "d1")
    assert row_html(client.get("/").text, lid) is not None
    resp = client.post(f"/listing/{lid}/dismiss")
    assert resp.status_code == 200 and resp.text == ""
    assert db.get_listing(conn, lid)["dismissed"] == 1
    assert row_html(client.get("/").text, lid) is None


# --- manual scans and the default factory -------------------------------------


def test_manual_scan_uses_fresh_connection_and_logs_errors(cfg, fake, caplog):
    built: list[sqlite3.Connection] = []

    def factory(c):
        built.append(c)
        return fake

    app = create_app(cfg, build_pipeline=factory, start_scheduler=False)
    with TestClient(app):
        runner = app.state.runner
        result = ScanResult(status="ok", items=[], unit_count_estimate=None, notes="",
                            model="m", input_tokens=1, output_tokens=1, cost_usd=0.1)
        fake.scan_error = SpendNotRecorded(7, result, False)
        runner.start_scan(7).join(5)
        fake.scan_error = RuntimeError("boom")
        runner.start_scan(8).join(5)
    assert fake.scanned == [(7, True), (8, True)]
    assert len(built) == 3  # the refresh pipeline, then one per scan
    for c in built[1:]:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
    assert "was not recorded" in caplog.text
    assert "boom" in caplog.text


def test_default_factory_wires_optional_features(cfg, conn):
    pipeline = default_pipeline_factory(cfg)(conn)
    assert set(pipeline.sources) == {"shopgoodwill", "hibid"}
    assert pipeline.scanner is None
    assert pipeline.notifier is None

    cfg.ebay_client_id = "id"
    assert "ebay" not in default_pipeline_factory(cfg)(conn).sources
    cfg.ebay_client_secret = "secret"
    cfg.anthropic_api_key = "sk-ant-test"
    cfg.discord_webhook_url = "https://discord.com/api/webhooks/1/TOKEN"
    factory = default_pipeline_factory(cfg)
    pipeline = factory(conn)
    assert set(pipeline.sources) == {"ebay", "shopgoodwill", "hibid"}
    assert pipeline.scanner is not None
    assert pipeline.notifier is not None
    factory.close()
