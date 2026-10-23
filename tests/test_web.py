import json
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
from dealfinder.catalog import load_catalog
from dealfinder.models import Classified, Comp, Listing, MarketValue, ScanResult, Scored
from dealfinder.pipeline import RefreshReport, SpendNotRecorded
from dealfinder.web.app import create_app, default_pipeline_factory

CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"
# The app only accepts state-changing requests sent by htmx to a local Host.
BASE_URL = "http://127.0.0.1:8000"
HTMX = {"HX-Request": "true"}


class HtmxClient(TestClient):
    """A local client whose POSTs come from htmx, like the dashboard's."""

    def __init__(self, app, **kwargs):
        super().__init__(app, base_url=BASE_URL, **kwargs)

    def post(self, url, *, headers=None, **kwargs):
        return super().post(url, headers={**HTMX, **(headers or {})}, **kwargs)


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
        self.scan_result: ScanResult | None = None
        self.scanned: list[tuple[int, bool]] = []
        self.scan_done = threading.Event()

    def refresh(self):
        self.calls += 1
        self.started.set()
        if self.block:
            self.release.wait(5)
        return RefreshReport()

    def scan_listing(self, listing_id, manual):
        self.scanned.append((listing_id, manual))
        try:
            if self.scan_error is not None:
                raise self.scan_error
            return self.scan_result
        finally:
            self.scan_done.set()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=str(tmp_path / "test.db"), catalog_dir=CATALOG_DIR)


@pytest.fixture
def fake():
    return FakePipeline()


@pytest.fixture
def client(cfg, fake):
    app = create_app(cfg, build_pipeline=lambda conn: fake, start_scheduler=False)
    with HtmxClient(app) as c:
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
    assert '<script src="/static/htmx.min.js"></script>' in html
    assert "unpkg.com" not in html
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
    with HtmxClient(app):
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
    with HtmxClient(app) as client:
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
    with HtmxClient(app):
        assert fake.started.wait(5)  # the first refresh runs right after startup
        jobs = app.state.scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].trigger.interval == timedelta(minutes=cfg.refresh_minutes)
    assert not app.state.scheduler.running


def test_header_status_polls_only_while_refreshing(cfg, fake):
    fake.block = True
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with HtmxClient(app) as client:
        assert 'hx-trigger="every 3s"' not in client.get("/header/status").text
        started = client.post("/refresh").text
        assert 'hx-trigger="every 3s"' in started and 'id="header-status"' in started
        assert fake.started.wait(5)
        polled = client.get("/header/status").text
        assert 'hx-trigger="every 3s"' in polled and "Refreshing" in polled
        assert 'hx-trigger="every 3s"' in client.get("/").text
        fake.release.set()
        app.state.runner.thread.join(5)
        assert 'hx-trigger="every 3s"' not in client.get("/header/status").text


def test_polled_status_never_replaces_the_controls(client, conn):
    run = db.start_run(conn, "hibid", now_utc())
    db.finish_run(conn, run, "site_change", 0, None, now_utc())
    polled = client.get("/header/status").text
    assert "HiBid" in polled and "site change" in polled  # runs and banners refresh
    for control in ("/controls/caps", 'name="daily"', "/controls/scan", "/controls/pause"):
        assert control not in polled
    # The status block sits inside the header, apart from the controls it never swaps.
    page = client.get("/").text
    status = re.search(r'<div id="header-status".*', page, re.DOTALL).group(0)
    assert "/controls/caps" not in status


def test_shutdown_closes_shared_clients(cfg):
    cfg.discord_webhook_url = "https://discord.com/api/webhooks/1/TOKEN"
    factory = default_pipeline_factory(cfg)
    app = create_app(cfg, build_pipeline=factory, start_scheduler=False)
    with HtmxClient(app):
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
    with HtmxClient(app) as client:
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
    with HtmxClient(app) as client:
        banner = client.get("/").text
    assert "EBAY_CLIENT_ID" in banner and "EBAY_CLIENT_SECRET" in banner
    assert "ANTHROPIC_API_KEY" in banner
    assert "DISCORD_WEBHOOK_URL" in banner

    cfg.ebay_client_id = "id-secret-value"
    cfg.ebay_client_secret = "ebay-secret-value"
    cfg.anthropic_api_key = "sk-ant-secret-value"
    cfg.discord_webhook_url = "https://discord.com/api/webhooks/1/hook-secret-value"
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with HtmxClient(app) as client:
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
    with HtmxClient(app):
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


# --- lot pages, listing detail, manual scans and catalog (task 18) ------------


def seed_lot(conn, source_id, *, kind="hardware_lot", key="ps2-console", category="console",
             quantity=4, title=None, price=80.0, end_in_h=None, profit=80.0, deal=True,
             discount=0.4, images=None):
    listing = Listing(
        source="ebay", source_id=source_id, title=title or f"Lot {source_id}",
        url=f"https://example.com/{source_id}", price=price, shipping=20.0,
        shipping_offered=True,
        image_urls=images if images is not None else [f"https://img.example.com/{source_id}.jpg"],
        is_auction=end_in_h is not None,
        end_time=now_utc() + timedelta(hours=end_in_h) if end_in_h is not None else None)
    c = Classified(kind=kind, key=key, condition=None, quantity=quantity, category=category)
    s = None
    if kind != "scan_only":
        s = Scored(total_cost=price + 20.0, shipping_estimated=False, profit=profit,
                   discount_pct=discount, is_target_hit=False, is_deal=deal,
                   rank_score=profit)
    return db.upsert_listing(conn, listing, c, s, now_utc() - timedelta(hours=1))


def add_scan(conn, listing_id, *, status="ok", items=(), photos=(), rare=False, lot=False,
             error=None, at=None, estimate=None, identified=None):
    conn.execute(
        "INSERT INTO scans (listing_id, status, model, items, unit_count_estimate,"
        " identified_value, is_rare_find, is_lot_find, cost_usd, scanned_at, error, photo_urls)"
        " VALUES (?, ?, 'claude-opus-5-5', ?, ?, ?, ?, ?, 0.12, ?, ?, ?)",
        (listing_id, status, json.dumps(list(items)), estimate, identified, int(rare),
         int(lot), (at or now_utc()).isoformat(), error, json.dumps(list(photos))))
    conn.commit()


def item(title, *, platform="PS2", completeness="loose", confidence="high", photo_index=0,
         value=None, low=None, edition=None):
    return {"kind": "game", "platform": platform, "title": title, "edition": edition,
            "completeness": completeness, "quantity": 1, "confidence": confidence,
            "photo_index": photo_index, "value": value, "value_low_confidence": low}


def save_value(conn, key, condition, value, comps=(), count=6, low=False):
    mv = MarketValue(key=key, condition=condition, value=value, comp_count=count,
                     method="sold", low_confidence=low, computed_at=now_utc())
    db.save_market_value(conn, mv, list(comps))


def test_nav_links_on_every_page(client):
    for path in ("/", "/lots/hardware", "/lots/games", "/catalog"):
        html = client.get(path).text
        nav = re.search(r"<nav>.*?</nav>", html, re.DOTALL).group(0)
        for href, label in (("/", "Deals"), ("/lots/hardware", "Bulk lots"),
                            ("/lots/games", "Game lots"), ("/catalog", "Catalog")):
            assert f'<a href="{href}"' in nav and label in nav


def test_hardware_lots_page(client, conn):
    plain = seed_lot(conn, "h1", title="4 PS2 consoles untested", quantity=4, price=80.0,
                     profit=81.0)
    find = seed_lot(conn, "h2", title="PS2 console lot", quantity=5, price=130.0, deal=False)
    add_scan(conn, find, rare=True)
    game = seed_lot(conn, "g1", kind="game_lot", key="ps2", category=None, quantity=20)

    resp = client.get("/lots/hardware")
    assert resp.status_code == 200
    html = resp.text
    row = row_html(html, plain)
    assert row is not None and "4 PS2 consoles untested" in row
    assert "PlayStation 2 console" in row  # bulk product name
    assert re.search(r">\s*4\s*<", row)  # quantity
    assert "$25.00" in row  # cost per unit: (80 + 20) / 4, also the console repair default
    assert "$81.00" in row  # expected profit
    assert "60%" in row  # console success rate default
    assert f'href="/listing/{plain}"' in row
    assert "💎" not in row
    assert "💎" in row_html(html, find)  # a photo find shows even when not a text deal
    assert row_html(html, game) is None

    html = client.get("/lots/hardware", params={"photo_finds_only": "1"}).text
    assert row_html(html, find) is not None
    assert row_html(html, plain) is None

    # The repair assumptions shown follow catalog overrides.
    db.set_override(conn, "bulk_overrides", "ps2-console", repair_cost=12.5, success_rate=0.8)
    row = row_html(client.get("/lots/hardware").text, plain)
    assert "$12.50" in row and "80%" in row

    partial = client.get("/lots/hardware", params={"photo_finds_only": "1"},
                         headers={"HX-Request": "true"})
    assert "<html" not in partial.text and row_html(partial.text, find) is not None


def test_game_lots_page_per_game_columns(client, conn):
    ps2 = seed_lot(conn, "g1", kind="game_lot", key="ps2", category=None, quantity=20,
                   price=40.0, profit=55.0, end_in_h=30, title="20 PS2 games lot")
    n64 = seed_lot(conn, "g2", kind="game_lot", key="n64", category=None, quantity=8,
                   price=60.0, end_in_h=2)
    find = seed_lot(conn, "g3", kind="game_lot", key="ps2", category=None, quantity=10,
                    price=50.0, deal=False, end_in_h=50)
    add_scan(conn, find, lot=True)
    hw = seed_lot(conn, "h1")
    save_value(conn, "ps2", "per_game", 5.5)

    html = client.get("/lots/games").text
    row = row_html(html, ps2)
    assert "PlayStation 2" in row  # platform name
    assert re.search(r">\s*20\s*<", row)  # game count
    assert "$3.00" in row  # cost per game: (40 + 20) / 20
    assert "$5.50" in row  # market per game
    assert "$55.00" in row  # expected profit
    assert "💎" in row_html(html, find) and "💎" not in row
    assert row_html(html, hw) is None

    def ids(params):
        page = client.get("/lots/games", params=params).text
        return {i for i in (ps2, n64, find) if row_html(page, i) is not None}

    assert ids({}) == {ps2, n64, find}
    assert ids({"platform": "ps2"}) == {ps2, find}
    assert ids({"ending_within_h": "3"}) == {n64}
    assert ids({"photo_finds_only": "1"}) == {find}
    assert ids({"platform": "", "ending_within_h": ""}) == {ps2, n64, find}


def test_listing_detail_shows_comps_and_scan_items(client, conn):
    lid = seed_lot(conn, "g1", kind="game_lot", key="ps2", category=None, quantity=20,
                   price=40.0, title="PS2 lot <b>bold</b>")
    comps = [Comp(title="Lot of 10 PS2 games <script>x</script>", price=55.0,
                  sold_date=None, url="https://www.ebay.com/itm/1", condition_label="Used"),
             Comp(title="PS2 games bundle 12", price=60.0, sold_date=None,
                  url="javascript:alert(1)", condition_label=None)]
    save_value(conn, "ps2", "per_game", 5.5, comps)
    photos = ["https://img.example.com/p0.jpg", "javascript:alert(2)",
              "https://img.example.com/p2.jpg"]
    add_scan(conn, lid, photos=photos, rare=True, identified=95.0, estimate=20, items=[
        item("Silent Hill 2", completeness="cib", photo_index=2, value=80.0),
        item("Madden 2004", confidence="low", photo_index=1, value=2.0, low=True),
        item("<i>Ico</i>", confidence="medium", photo_index=-1),
        item("Kingdom Hearts", photo_index=7, value=9.0),
    ])

    resp = client.get(f"/listing/{lid}")
    assert resp.status_code == 200
    html = resp.text
    assert "PS2 lot &lt;b&gt;bold&lt;/b&gt;" in html
    assert "price $40.00 + shipping $20.00" in html  # cost breakdown
    assert "$5.50" in html  # the per-game value behind the score
    assert "Lot of 10 PS2 games &lt;script&gt;x&lt;/script&gt;" in html
    assert 'href="https://www.ebay.com/itm/1"' in html
    assert "PS2 games bundle 12" in html
    assert "javascript:" not in html  # unsafe URLs are never rendered
    # Photos from the scan, beside the identified items.
    assert 'src="https://img.example.com/p0.jpg"' in html
    assert 'src="https://img.example.com/p2.jpg"' in html
    items = re.search(r'<table class="deals items".*?</table>', html, re.DOTALL).group(0)
    silent = re.search(r"<tr>(?:(?!</tr>).)*Silent Hill 2.*?</tr>", items, re.DOTALL).group(0)
    assert 'href="#photo-2"' in silent and "Photo 3" in silent
    assert "cib" in silent and "high" in silent and "$80.00" in silent
    madden = re.search(r"<tr>(?:(?!</tr>).)*Madden 2004.*?</tr>", items, re.DOTALL).group(0)
    assert "#photo-1" not in madden  # its photo URL failed the guard
    assert "$2.00" in madden and "low" in madden
    ico = re.search(r"<tr>(?:(?!</tr>).)*&lt;i&gt;Ico.*?</tr>", items, re.DOTALL).group(0)
    assert "#photo" not in ico and "medium" in ico
    kh = re.search(r"<tr>(?:(?!</tr>).)*Kingdom Hearts.*?</tr>", items, re.DOTALL).group(0)
    assert "#photo" not in kh  # index out of range
    assert "💎" in html
    assert "Scan this lot" not in html  # already scanned

    assert client.get("/listing/999999").status_code == 404


def test_scan_button_only_for_unscanned_live_lots(cfg, fake, conn):
    cfg.anthropic_api_key = "sk-ant-test"
    unscanned = seed_lot(conn, "u1", end_in_h=5)
    scan_only = seed_lot(conn, "u2", kind="scan_only", quantity=None)
    ended = seed_lot(conn, "u3", end_in_h=-1)
    single = seed(conn, "s1")
    scanned = seed_lot(conn, "u4")
    add_scan(conn, scanned, status="failed", error="no usable photos")
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with HtmxClient(app) as client:
        for lid in (unscanned, scan_only):
            html = client.get(f"/listing/{lid}").text
            assert "Scan this lot" in html and f'hx-post="/listing/{lid}/scan"' in html
        for lid in (ended, single, scanned):
            assert "Scan this lot" not in client.get(f"/listing/{lid}").text
        assert "no usable photos" in client.get(f"/listing/{scanned}").text

    cfg.anthropic_api_key = None
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with HtmxClient(app) as client:
        html = client.get(f"/listing/{unscanned}").text
    assert "Scan this lot" not in html and "ANTHROPIC_API_KEY" in html


def test_manual_scan_denied_shows_reason(client, cfg, conn, fake, monkeypatch):
    lid = seed_lot(conn, "m1")
    started: list[int] = []
    monkeypatch.setattr(client.app.state.runner, "start_scan", started.append)
    Budget(conn, cfg, local_now).set_caps(daily=0.0)
    resp = client.post(f"/listing/{lid}/scan")
    assert resp.status_code == 200
    assert "daily cap reached" in resp.text
    assert "Scanning" not in resp.text
    assert started == [] and fake.scanned == []


def test_manual_scan_runs_when_switch_off(client, cfg, conn, fake):
    lid = seed_lot(conn, "m1")
    assert Budget(conn, cfg, local_now).scan_enabled() is False
    resp = client.post(f"/listing/{lid}/scan")
    assert resp.status_code == 200
    assert "Scanning…" in resp.text
    assert fake.scan_done.wait(5)
    assert fake.scanned == [(lid, True)]


def test_manual_scan_error_shown_until_next_successful_scan(client, fake):
    runner = client.app.state.runner
    result = ScanResult(status="ok", items=[], unit_count_estimate=None, notes="",
                        model="m", input_tokens=1, output_tokens=1, cost_usd=0.1)
    fake.scan_error = SpendNotRecorded(7, result, False)
    runner.start_scan(7).join(5)
    status = client.get("/header/status").text
    assert "spend not recorded — caps may be off by one scan" in status
    assert 'href="/listing/7"' in status

    fake.scan_error = RuntimeError("boom <x>")
    runner.start_scan(8).join(5)
    status = client.get("/header/status").text
    assert "boom &lt;x&gt;" in status and "spend not recorded" not in status

    fake.scan_error = None
    fake.scan_result = None  # the scan did not run: the error stays
    runner.start_scan(9).join(5)
    assert "boom" in client.get("/header/status").text
    fake.scan_result = result
    runner.start_scan(9).join(5)
    assert "boom" not in client.get("/header/status").text


def test_scan_failure_banner(client, conn):
    failed = seed_lot(conn, "f1")
    refused = seed_lot(conn, "f2")
    old = seed_lot(conn, "f3")
    ok = seed_lot(conn, "f4")
    add_scan(conn, failed, status="failed", error="api error")
    add_scan(conn, refused, status="refused")
    add_scan(conn, old, status="failed", at=now_utc() - timedelta(hours=25))
    add_scan(conn, ok)
    status = client.get("/header/status").text
    banner = re.search(r'<div class="banner".*?</div>', status, re.DOTALL).group(0)
    assert "2 photo scans failed in the last 24 h" in banner
    assert f'href="/listing/{failed}"' in banner and f'href="/listing/{refused}"' in banner
    assert f'href="/listing/{old}"' not in banner and f'href="/listing/{ok}"' not in banner


def catalog_row(html, table, item_id):
    m = re.search(rf'<tr id="catalog-{table}-{re.escape(item_id)}".*?</tr>', html, re.DOTALL)
    return m.group(0) if m else None


def test_catalog_page_sections(client, conn):
    save_value(conn, "rtx-4090", "used", 1500.0)
    save_value(conn, "ps2-console", "used", 45.0, count=3, low=True)
    save_value(conn, "ps2", "per_game", 5.5)
    html = client.get("/catalog").text
    assert "$1,500.00" in catalog_row(html, "product", "rtx-4090")
    assert "GeForce RTX 4090" in catalog_row(html, "bulk", "bulk-rtx-4090")
    bulk = catalog_row(html, "bulk", "ps2-console")
    assert "$45.00" in bulk and "low" in bulk
    assert 'placeholder="25.00"' in bulk and 'placeholder="0.60"' in bulk  # defaults
    assert "$5.50" in catalog_row(html, "platform", "ps2")
    assert 'hx-post="/catalog/custom/product"' in html
    assert 'hx-post="/catalog/custom/bulk"' in html


def test_catalog_override_saved(client, cfg, conn):
    resp = client.post("/catalog/product/rtx-4090", data={"enabled": "true", "max_price": "1200"})
    assert resp.status_code == 200
    assert 'id="catalog-product-rtx-4090"' in resp.text and 'value="1200.00"' in resp.text
    product = load_catalog(cfg.catalog_dir, conn, cfg).product("rtx-4090")
    assert product.max_price == 1200.0 and product.enabled

    resp = client.post("/catalog/bulk/ps2-console",
                       data={"enabled": "true", "repair_cost": "30", "success_rate": "0.5"})
    assert resp.status_code == 200
    bulk = load_catalog(cfg.catalog_dir, conn, cfg).bulk_product("ps2-console")
    assert (bulk.repair_cost, bulk.success_rate) == (30.0, 0.5)

    resp = client.post("/catalog/platform/ps2", data={})  # unchecked switch: disabled
    assert resp.status_code == 200
    assert not load_catalog(cfg.catalog_dir, conn, cfg).platform("ps2").enabled

    # A blank field clears the override back to the default.
    client.post("/catalog/bulk/ps2-console", data={"enabled": "true", "repair_cost": "",
                                                    "success_rate": "0.5"})
    assert load_catalog(cfg.catalog_dir, conn, cfg).bulk_product("ps2-console").repair_cost == 25

    for path, data, message in (
        ("/catalog/bulk/ps2-console", {"success_rate": "1.5"}, "success rate"),
        ("/catalog/bulk/ps2-console", {"repair_cost": "-1"}, "repair cost"),
        ("/catalog/bulk/ps2-console", {"repair_cost": "nan"}, "repair cost"),
        ("/catalog/product/rtx-4090", {"max_price": "abc"}, "max price"),
        ("/catalog/product/rtx-4090", {"max_price": "-5"}, "max price"),
    ):
        resp = client.post(path, data={"enabled": "true", **data})
        assert resp.status_code == 400, data
        assert message in resp.text and 'class="error"' in resp.text
    catalog = load_catalog(cfg.catalog_dir, conn, cfg)
    assert catalog.bulk_product("ps2-console").success_rate == 0.5  # nothing written
    assert catalog.product("rtx-4090").max_price == 1200.0

    assert client.post("/catalog/nope/rtx-4090", data={}).status_code == 404
    assert client.post("/catalog/product/no-such-id", data={}).status_code == 404


def test_custom_product_bad_regex_rejected(client, conn):
    base = {"id": "my-card", "category": "gpu", "name": "My Card", "ebay_query": "my card",
            "match": r"\bmy (card"}
    resp = client.post("/catalog/custom/product", data=base)
    assert resp.status_code == 400
    assert "invalid regex" in resp.text and 'class="error"' in resp.text
    assert 'value="My Card"' in resp.text  # the form keeps what was typed
    assert db.list_custom(conn, "product") == []

    for kind, data, message in (
        ("product", {**base, "match": r"\bmy card\b", "exclude": "ok\n(bad"}, "invalid regex"),
        ("product", {**base, "match": r"\bmy\b", "id": "rtx-4090"}, "already exists"),
        ("product", {**base, "match": r"\bmy\b", "id": "My Card"}, "id"),
        ("product", {**base, "match": r"\bmy\b", "max_price": "-1"}, "max price"),
        ("bulk", {**base, "match": r"\bmy\b", "id": "bulk-rtx-4090"}, "already exists"),
        ("bulk", {**base, "match": r"\bmy\b", "id": "ps2-console"}, "already exists"),
        ("bulk", {**base, "match": r"\bmy\b", "success_rate": "2"}, "success rate"),
        ("bulk", {**base, "match": r"\bmy\b", "category": "phone"}, "category"),
    ):
        resp = client.post(f"/catalog/custom/{kind}", data=data)
        assert resp.status_code == 400, data
        assert message in resp.text, data
    assert db.list_custom(conn, "product") == [] and db.list_custom(conn, "bulk") == []
    assert client.post("/catalog/custom/nope", data=base).status_code == 404


def test_custom_product_added(client, cfg, conn):
    resp = client.post("/catalog/custom/product", data={
        "id": "my-card", "category": "gpu", "name": "My Card", "ebay_query": "my card",
        "match": r"\bmy card\b", "exclude": "broken\n\n  parts  ", "max_price": "300"})
    assert resp.status_code == 200
    assert resp.headers.get("HX-Refresh") == "true"
    [added] = db.list_custom(conn, "product")
    assert added["match"] == r"\bmy card\b" and added["exclude"] == ["broken", "parts"]
    assert added["max_price"] == 300.0
    html = client.get("/catalog").text
    assert "My Card" in catalog_row(html, "product", "my-card")
    assert catalog_row(html, "bulk", "bulk-my-card") is not None  # gpu: auto bulk product

    resp = client.post("/catalog/custom/bulk", data={
        "id": "my-console", "category": "console", "name": "My Console",
        "ebay_query": "my console", "match": r"\bmy console\b", "repair_cost": "",
        "success_rate": ""})
    assert resp.status_code == 200
    bulk = load_catalog(cfg.catalog_dir, conn, cfg).bulk_product("my-console")
    assert (bulk.repair_cost, bulk.success_rate) == (25.0, 0.6)  # category defaults
    # A gpu product whose generated bulk id is already a custom bulk id is rejected.
    resp = client.post("/catalog/custom/bulk", data={
        "id": "bulk-other", "category": "gpu", "name": "Other", "ebay_query": "other",
        "match": r"\bother\b", "repair_cost": "40", "success_rate": "0.4"})
    assert resp.status_code == 200
    resp = client.post("/catalog/custom/product", data={
        "id": "other", "category": "gpu", "name": "Other", "ebay_query": "other",
        "match": r"\bother\b"})
    assert resp.status_code == 400 and "bulk-other" in resp.text


# --- CSRF and origin protection ------------------------------------------------


def test_cross_origin_post_forbidden(client, cfg, conn):
    resp = client.post("/controls/scan", data={"on": "true"},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert Budget(conn, cfg, local_now).scan_enabled() is False


def test_post_without_hx_request_forbidden(cfg, fake, conn):
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with TestClient(app, base_url=BASE_URL) as plain:
        resp = plain.post("/controls/scan", data={"on": "true"},
                          headers={"Origin": BASE_URL})
    assert resp.status_code == 403
    assert Budget(conn, cfg, local_now).scan_enabled() is False


@pytest.mark.parametrize("host", ["evil.example:8000", "evil.example", "127.0.0.1",
                                  "127.0.0.1.evil.example:8000", "localhost:abc"])
def test_bad_host_forbidden(client, cfg, conn, fake, host):
    lid = seed_lot(conn, "c1")
    resp = client.post(f"/listing/{lid}/scan", headers={"Host": host})
    assert resp.status_code == 403
    assert fake.scanned == []


@pytest.mark.parametrize("origin", ["http://127.0.0.1:9999", "http://localhost:9999",
                                    "https://127.0.0.1:8000", "null"])
def test_origin_must_match_local_host_and_port(client, origin):
    resp = client.post("/controls/caps", headers={"Origin": origin},
                       data={"daily": "3", "monthly": "40", "max_scan_price": "200"})
    assert resp.status_code == 403


@pytest.mark.parametrize("host, origin", [
    ("127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("127.0.0.1:8000", "http://localhost:8000"),
    ("localhost:8123", "http://localhost:8123"),
    ("localhost:8123", None),
])
def test_htmx_post_from_local_origin_allowed(client, cfg, conn, host, origin):
    headers = {"Host": host, **({"Origin": origin} if origin else {})}
    resp = client.post("/controls/scan", data={"on": "true"}, headers=headers)
    assert resp.status_code == 200
    assert Budget(conn, cfg, local_now).scan_enabled() is True


def test_get_unaffected(cfg, fake):
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with TestClient(app) as plain:  # default "testserver" host, no htmx header
        assert plain.get("/").status_code == 200
        assert plain.get("/catalog", headers={"Origin": "https://evil.example"}).status_code == 200


def test_htmx_served_locally(client):
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert "htmx" in resp.text[:200]


def test_every_post_in_templates_uses_htmx():
    templates = Path(__file__).resolve().parent.parent / "dealfinder" / "web" / "templates"
    for path in templates.rglob("*.html"):
        html = path.read_text()
        assert not re.search(r"method=[\"']?post", html, re.IGNORECASE), path
        assert not re.search(r"<form(?![^>]*\bhx-(get|post)=)", html), path


# --- stale listings ------------------------------------------------------------------


def test_stale_listings_hidden(client, conn):
    assert client.app.state.cfg.ebay_full_pass_hours == 3
    stale = seed(conn, "old", title="RTX 4090 gone", first_seen=now_utc() - timedelta(hours=7))
    fresh = seed(conn, "new", title="RTX 4090 here", first_seen=now_utc() - timedelta(hours=1))
    html = client.get("/").text
    assert row_html(html, fresh) is not None and row_html(html, stale) is None
    old_lot = seed_lot(conn, "oldlot")
    conn.execute("UPDATE listings SET last_seen = ? WHERE id = ?",
                 ((now_utc() - timedelta(hours=7)).isoformat(), old_lot))
    conn.commit()
    new_lot = seed_lot(conn, "newlot")
    html = client.get("/lots/hardware").text
    assert f"/listing/{new_lot}" in html and f"/listing/{old_lot}" not in html
    client.app.state.cfg.anthropic_api_key = "sk-ant-test"
    assert "Scan this lot" in client.get(f"/listing/{new_lot}").text
    assert "Scan this lot" not in client.get(f"/listing/{old_lot}").text
