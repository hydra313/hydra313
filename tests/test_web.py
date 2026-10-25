import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from selectolax.lexbor import LexborHTMLParser

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
        self.imported = []

    def import_listings(self, listings):
        self.imported.extend(listings)
        return len(listings)

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
    default_age = timedelta(minutes=5) if source == "hibid" else timedelta(hours=1)
    return db.upsert_listing(conn, listing, c, s, first_seen or now_utc() - default_age)


def row_html(html, listing_id):
    m = re.search(rf'<tr id="listing-{listing_id}".*?</tr>', html, re.DOTALL)
    return m.group(0) if m else None


class AllListingsClient:
    """Explicit all-results view for tests unrelated to deal eligibility."""
    def __init__(self, client): self.client=client
    def __getattr__(self,name): return getattr(self.client,name)
    def get(self,url,**kwargs):
        from urllib.parse import urlsplit,parse_qsl,urlunsplit,urlencode
        parts=urlsplit(url)
        if parts.path in ('/','/lots/hardware','/lots/games'):
            params=dict(parse_qsl(parts.query))
            params.update(kwargs.pop('params',{}) or {})
            params.setdefault('show_all','true')
            url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(params),parts.fragment))
        return self.client.get(url,**kwargs)


# --- deals page ----------------------------------------------------------------


def test_explicit_all_view_lists_active_items(client, conn):
    client = AllListingsClient(client)
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
    assert row_html(html, hit) is not None
    assert "🎯" not in row_html(html, plain)
    assert row_html(html, gone) is None
    assert row_html(html, no_deal) is not None
    assert row_html(client.get('/?show_all=false').text, no_deal) is None
    assert "$520.00" in row_html(html, plain)  # total cost


def test_filters_query_params(client, conn):
    client = AllListingsClient(client)
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


@pytest.mark.parametrize("category,label,key", [
    ("gpu", "GPU", "rtx-4090"), ("cpu", "CPU", "i5-12400"),
    ("console", "Console", "ps5-spider-man-2"),
    ("controller", "Controller", "dualsense-30th-anniversary"),
])
def test_category_dropdown_values_and_old_capitalized_links_work(client, conn, category, label, key):
    client = AllListingsClient(client)
    lid = seed(conn, "category-"+category, category=category, key=key)
    page = LexborHTMLParser(client.get('/').text)
    option = next(n for n in page.css('select[name="category"] option') if n.text() == label)
    assert option.attributes['value'] == category
    for value in (option.attributes['value'], label, ' '+label+' '):
        response = client.get('/', params={'category':value})
        assert row_html(response.text,lid) is not None
        selected = LexborHTMLParser(response.text).css_first('select[name="category"] option[selected]')
        assert selected.attributes['value'] == category


def test_new_badge_uses_previous_visit(client, conn):
    client = AllListingsClient(client)
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


def test_paid_scan_controls_are_removed(client,cfg,conn):
    assert 'Photo scanning:' not in client.get('/').text
    assert client.post('/controls/scan',data={'on':'true'}).status_code == 410
    assert not Budget(conn,cfg,local_now).scan_enabled()


def test_paid_scan_budget_form_is_removed(client):
    assert client.post('/controls/caps',data={'daily':'3','monthly':'40'}).status_code == 410
    assert '/controls/caps' not in client.get('/').text


def test_scan_spend_does_not_clutter_header(client):
    html=client.get('/').text
    assert 'Scan budgets' not in html and 'scanning-control' not in html


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


def test_header_status_keeps_polling_between_automatic_refreshes(cfg, fake):
    fake.block = True
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with HtmxClient(app) as client:
        assert 'hx-trigger="every 10s"' in client.get("/header/status").text
        started = client.post("/refresh").text
        assert 'hx-trigger="every 3s"' in started and 'id="header-status"' in started
        assert fake.started.wait(5)
        polled = client.get("/header/status").text
        assert 'hx-trigger="every 3s"' in polled and "Refreshing" in polled
        assert 'hx-trigger="every 3s"' in client.get("/").text
        fake.release.set()
        app.state.runner.thread.join(5)
        finished = client.get("/header/status").text
        assert 'hx-trigger="every 10s"' in finished
        assert "Refresh complete" in finished


def test_polled_status_never_replaces_the_controls(client, conn, cfg):
    cfg.hibid_mode = "api"
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


def test_banner_on_site_change(client, conn, cfg):
    cfg.hibid_mode = "api"
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
    assert "eBay keyless mode" in banner and "/sources/ebay" in banner
    assert "ANTHROPIC_API_KEY" in banner
    assert "Connect Discord" in banner

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
    client = AllListingsClient(client)
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
    assert set(pipeline.sources) == {"ebay", "shopgoodwill", "hibid"}
    assert pipeline.sources["ebay"].browser_assisted
    assert pipeline.scanner is None
    assert not pipeline.notifier.enabled()  # supports connecting a channel without restarting

    cfg.ebay_client_id = "id"
    assert default_pipeline_factory(cfg)(conn).sources["ebay"].browser_assisted
    cfg.ebay_client_secret = "secret"
    cfg.anthropic_api_key = "sk-ant-test"
    cfg.discord_webhook_url = "https://discord.com/api/webhooks/1/TOKEN"
    factory = default_pipeline_factory(cfg)
    pipeline = factory(conn)
    assert set(pipeline.sources) == {"ebay", "shopgoodwill", "hibid"}
    assert pipeline.scanner is None  # removed even if an old API key remains
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
    for path in ("/", "/lots/hardware", "/catalog", "/connections"):
        html = client.get(path).text
        nav = LexborHTMLParser(html).css_first('nav[aria-label="Main navigation"]')
        for href, label in (("/", "Deals"), ("/lots/hardware", "Bulk lots"),
                            ("/catalog", "Catalog"),
                            ("/connections", "Connections")):
            link = nav.css_first(f'a[href="{href}"]')
            assert link is not None and label in link.text()
        active = nav.css('a[aria-current="page"]')
        assert len(active) == 1 and active[0].attributes["href"] == path


def test_hardware_lots_page(client, conn):
    client = AllListingsClient(client)
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


def test_retired_game_lot_page_redirects_without_rendering_old_results(client,conn):
    lid=seed_lot(conn,'old-game',kind='game_lot',key='ps2',category=None,quantity=20)
    response=client.get('/lots/games?show_all=true',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/'
    assert row_html(client.get('/?show_all=true').text,lid) is None
    assert db.get_listing(conn,lid) is not None  # history is preserved


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
    assert 'Photo scan' not in html and 'Scan this lot' not in html
    assert 'Silent Hill 2' not in html  # historical paid-scan output is not a product feature

    assert client.get("/listing/999999").status_code == 404


def test_photo_feature_removed_even_with_existing_api_key(cfg,fake,conn):
    cfg.anthropic_api_key='sk-ant-test'
    lid=seed_lot(conn,'old-scan')
    with HtmxClient(create_app(cfg,build_pipeline=lambda c:fake,start_scheduler=False)) as client:
        assert 'Scan this lot' not in client.get(f'/listing/{lid}').text
        assert client.post(f'/listing/{lid}/scan').status_code == 410
    assert fake.scanned == []


def test_removed_scan_never_starts(client,conn,fake):
    lid=seed_lot(conn,'removed-scan')
    assert client.post(f'/listing/{lid}/scan').status_code == 410
    assert fake.scanned == []


def test_removed_scan_cannot_be_enabled_with_a_key(client,cfg,conn,fake):
    cfg.anthropic_api_key='sk-ant-test'
    lid=seed_lot(conn,'removed-scan-key')
    assert client.post(f'/listing/{lid}/scan').status_code == 410
    assert fake.scanned == []


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
    assert catalog_row(html, "platform", "ps2") is None
    assert 'hx-post="/catalog/custom/product"' in html
    assert 'hx-post="/catalog/custom/bulk"' in html


def test_large_catalog_can_be_searched_and_paged(client):
    page = LexborHTMLParser(client.get('/catalog').text)
    first = {n.attributes['id'] for n in page.css('tr[id^="catalog-product-"]')}
    assert 0 < len(first) <= 200
    second = LexborHTMLParser(client.get('/catalog?page=2').text)
    following = {n.attributes['id'] for n in second.css('tr[id^="catalog-product-"]')}
    assert following and not first.intersection(following)
    results = LexborHTMLParser(client.get('/catalog?q=samus').text)
    names = [n.text() for n in results.css('tr[id^="catalog-product-"] td.title')]
    assert names and all('samus' in name.casefold() for name in names)
    assert results.css_first('a[href^="https://consolevariations.com/collectibles/"]') is not None
    assert client.get('/catalog?q=%3Cscript%3E').status_code == 200


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
    assert resp.status_code == 410
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
    assert resp.status_code == 410
    assert Budget(conn, cfg, local_now).scan_enabled() is False


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
        html = path.read_text(encoding='utf-8')
        assert not re.search(r"method=[\"']?post", html, re.IGNORECASE), path
        assert not re.search(r"<form(?![^>]*\bhx-(get|post)=)", html), path


# --- stale listings ------------------------------------------------------------------


def test_stale_listings_hidden(client, conn):
    client = AllListingsClient(client)
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
    assert "Scan this lot" not in client.get(f"/listing/{new_lot}").text
    assert "Scan this lot" not in client.get(f"/listing/{old_lot}").text


def test_unseen_auction_with_known_end_still_shown(client, conn):
    client = AllListingsClient(client)
    auction = seed(conn, "auc", title="RTX 4090 auction", end_in_h=5,
                   first_seen=now_utc() - timedelta(hours=7))
    lot = seed_lot(conn, "auclot", end_in_h=5)
    conn.execute("UPDATE listings SET last_seen = ? WHERE id = ?",
                 ((now_utc() - timedelta(hours=7)).isoformat(), lot))
    conn.commit()
    assert row_html(client.get("/").text, auction) is not None
    assert f"/listing/{lot}" in client.get("/lots/hardware").text
    client.app.state.cfg.anthropic_api_key = "sk-ant-test"
    assert "Scan this lot" not in client.get(f"/listing/{lot}").text


def test_show_all_exposes_unpriced_singles_but_keeps_other_filters(client, conn):
    listing_id = seed(conn, "unpriced", deal=False)
    conn.execute("UPDATE listings SET profit = NULL, discount_pct = NULL WHERE id = ?",
                 (listing_id,))
    conn.commit()
    dismissed = seed(conn, "dismissed-unpriced", deal=False)
    db.dismiss_listing(conn, dismissed)
    assert row_html(client.get("/?show_all=false").text, listing_id) is None
    html = client.get("/", params={"show_all": "true"}).text
    assert row_html(html, listing_id) is not None
    assert row_html(html, dismissed) is None
    assert 'name="show_all" value="true" checked' in html
    assert row_html(client.get("/", params={"show_all": "true", "category": "cpu"}).text,
                    listing_id) is None
    partial = client.get("/?show_all=true", headers=HTMX).text
    assert "<html" not in partial and row_html(partial, listing_id) is not None


def test_show_all_exposes_unknown_quantity_lots_for_manual_scan(client, conn):
    hardware = seed_lot(conn, "unknown-hw", kind="scan_only", quantity=None)
    games = seed_lot(conn, "unknown-games", kind="scan_only", key="ps2", category=None,
                     quantity=None)
    conn.execute("UPDATE listings SET is_deal = 0, total_cost = NULL WHERE id IN (?, ?)",
                 (hardware, games))
    conn.commit()
    for path, included, excluded in (("/lots/hardware", hardware, games),):
        assert row_html(client.get(path, params={"show_all": "false"}).text, included) is None
        html = client.get(path, params={"show_all": "true"}).text
        assert row_html(html, included) is not None
        assert row_html(html, excluded) is None
        assert 'name="show_all" value="true" checked' in html


@pytest.mark.parametrize("path,kind,key,category", [
    ("/", "single", "rtx-4090", "gpu"),
    ("/lots/hardware", "hardware_lot", "ps2-console", "console"),
])
def test_show_all_defaults_and_explicit_checkbox_values(client, conn, path, kind, key, category):
    lid = (seed(conn, "default-all", deal=False) if kind == "single" else
           seed_lot(conn, "default-all", kind=kind, key=key, category=category, deal=False))
    for headers in ({}, HTMX):
        assert row_html(client.get(path, headers=headers).text, lid) is None
        assert row_html(client.get(path + "?show_all=false", headers=headers).text, lid) is None
        # A checked checkbox follows the hidden false value in the form submission.
        assert row_html(client.get(path + "?show_all=false&show_all=true", headers=headers).text,
                        lid) is not None
    html = LexborHTMLParser(client.get(path).text)
    assert html.css_first('input[type="hidden"][name="show_all"]').attributes['value'] == 'false'
    assert 'checked' not in html.css_first('input[type="checkbox"][name="show_all"]').attributes


def test_sold_lookup_failure_is_visible_and_escaped(client, conn):
    from dealfinder.market import SOLD_ERROR_SETTING

    db.set_setting(conn, SOLD_ERROR_SETTING, "HTTP 403 <blocked>")
    html = client.get("/header/status").text
    assert "eBay sold-price lookup failed" in html
    assert "HTTP 403 &lt;blocked&gt;" in html
    assert "Show all listings" in html
    db.set_setting(conn, SOLD_ERROR_SETTING, "")
    assert "eBay sold-price lookup failed" not in client.get("/header/status").text


def test_refresh_completion_updates_rows_without_reloading(client, fake):
    response = client.get("/header/status")
    assert response.headers["HX-Trigger"] == "listings-updated"
    for path in ("/", "/lots/hardware", "/lots/games"):
        assert "listings-updated from:body" in client.get(path).text
    fake.block = True
    runner = client.app.state.runner
    runner.start_refresh()
    assert fake.started.wait(5)
    try:
        assert client.get("/header/status").headers["HX-Trigger"] == "listings-updated"
    finally:
        fake.release.set()
        runner.thread.join(5)
    assert client.get("/header/status").headers["HX-Trigger"] == "listings-updated"


def test_all_legacy_scan_links_are_retired(client,conn,fake):
    lid=seed_lot(conn,'retired-scan')
    for listing_id in (lid,999999):
        assert client.post(f'/listing/{listing_id}/scan').status_code == 410
    assert fake.scanned == []


def test_hibid_browser_page_and_import(client, cfg, conn, fake):
    from dealfinder.sources.hibid_browser import snapshot_status
    page = client.get("/sources/hibid")
    assert page.status_code == 200
    assert "Open HiBid" in page.text and "captured_at" in page.text
    assert "browser import" in client.get("/header/status").text.lower()
    html = (Path(__file__).parent / "fixtures/hibid/browser_search.html").read_text(encoding="utf-8")
    response = client.post("/sources/hibid/import", data={"page_html": html, "query": "rtx",
                           "captured_at": local_now().isoformat()})
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/sources/hibid"
    assert len(fake.imported) == 2
    assert snapshot_status(conn, cfg, now_utc())["state"] == "ready"
    assert "HTTP 403" not in client.get("/header/status").text


@pytest.mark.parametrize("capture", ["bad time", "", "2020-01-01T00:00", "2099-01-01T00:00"])
def test_hibid_import_rejects_invalid_or_stale_capture_time(client, capture, fake):
    response = client.post("/sources/hibid/import", data={"captured_at": capture,
                           "page_html": "<html>results</html>"})
    assert response.status_code == 400
    assert fake.imported == []


def test_hibid_import_html_file_and_csrf(client, fake):
    html = (Path(__file__).parent / "fixtures/hibid/browser_search.html").read_bytes()
    response = client.post("/sources/hibid/import", data={"query": "rtx",
                           "captured_at": local_now().isoformat()},
                           files={"page_file": ("results.html", html, "text/html")})
    assert response.status_code == 200 and len(fake.imported) == 2
    response = client.post("/sources/hibid/import", headers={"Origin": "https://hibid.com"},
                           data={"page_html": "x", "captured_at": local_now().isoformat()})
    assert response.status_code == 403


def test_hibid_import_accepts_html_over_one_megabyte(client, fake):
    html = (Path(__file__).parent / "fixtures/hibid/browser_search.html").read_text(encoding="utf-8")
    response = client.post("/sources/hibid/import", data={"query": "rtx",
                           "captured_at": local_now().isoformat(), "page_html": html + " " * 1500000})
    assert response.status_code == 200, response.text[:300]
    assert len(fake.imported) == 2


def test_hibid_import_rejects_empty_results_and_large_upload(client, fake):
    html = (Path(__file__).parent / "fixtures/hibid/browser_search.html").read_text(encoding="utf-8")
    response = client.post("/sources/hibid/import", data={"page_html": html.replace("USD", "CAD"),
                           "captured_at": local_now().isoformat()})
    assert response.status_code == 400
    assert "usable" in response.text
    response = client.post("/sources/hibid/import", data={"page_html": "x" * (5 * 1024 * 1024 + 1),
                           "captured_at": local_now().isoformat()})
    assert response.status_code == 413
    assert fake.imported == []


def test_expired_hibid_prices_hide_and_cannot_be_scanned(client, cfg, conn, fake):
    lid = seed(conn, "old-hibid", source="hibid", end_in_h=24,
               first_seen=now_utc() - timedelta(minutes=31))
    lot = seed_lot(conn, "old-hibid-lot", end_in_h=24)
    conn.execute("UPDATE listings SET source = 'hibid', last_seen = ? WHERE id = ?",
                 ((now_utc() - timedelta(minutes=31)).isoformat(), lot))
    conn.commit()
    assert row_html(client.get("/?show_all=true").text, lid) is None
    assert row_html(client.get("/lots/hardware?show_all=true").text, lot) is None
    cfg.anthropic_api_key = "test"
    assert "Scan this lot" not in client.get(f"/listing/{lot}").text
    assert client.post(f"/listing/{lot}/scan").status_code == 410
    assert fake.scanned == []

# Keyless helper requests have a narrow, local capability; normal CSRF stays enforced.
def test_hibid_helper_rotates_queries_imports_and_obeys_security_pause(client, cfg, conn, fake):
    assert client.post('/browser/hibid/next').status_code == 403
    headers = {'X-Dealfinder-Token':client.app.state.browser_token}
    first = client.post('/browser/hibid/next', headers=headers).json()
    second = client.post('/browser/hibid/next', headers=headers).json()
    assert first['url'].startswith('https://hibid.com/lots?') and first['query'] != second['query']
    html = (Path(__file__).parent / 'fixtures/hibid/browser_search.html').read_text(encoding='utf-8')
    response = client.post('/browser/hibid/results', headers=headers,
                          json={'query':first['query'], 'html':html, 'captured_at':now_utc().isoformat()})
    assert response.status_code == 200 and response.json()['usable'] == 2
    assert {l.source for l in fake.imported} == {'hibid'} and not fake.scanned
    Budget(conn,cfg,local_now).set_paused(True)
    assert client.post('/browser/hibid/next',headers=headers).json()['paused']


def test_hibid_automatic_empty_search_continues_and_stale_capture_rejected(client, conn):
    headers = {'X-Dealfinder-Token':client.app.state.browser_token}
    for html in ('<p>No lots found</p>', '<div class="app-auction-lot-card"><a href="/lot/123">Pickup only</a></div>'):
        response = client.post('/browser/hibid/results',headers=headers,
                               json={'query':'rtx','html':html,'captured_at':now_utc().isoformat()})
        assert response.status_code == 200 and response.json()['usable'] == 0
    assert client.post('/browser/hibid/results',headers=headers,
                       json={'html':'No lots found','captured_at':(now_utc()-timedelta(minutes=5)).isoformat()}).status_code == 400
    assert client.post('/browser/hibid/results',headers=headers,json={'html':'Loading','captured_at':now_utc().isoformat()}).status_code == 400


def test_connections_page_and_shared_search_controls(client,conn):
    client = AllListingsClient(client)
    page = client.get('/connections')
    assert page.status_code == 200 and 'Discord' in page.text and 'Connect HiBid' in page.text
    for path in ('/', '/lots/hardware', '/lots/games'):
        page = LexborHTMLParser(client.get(path).text)
        for field in ('q','brand','console','condition','sort','direction','min_price','max_price','rating'):
            assert page.css_first(f'[name="{field}"]') is not None
    a=seed(conn,'search-sony',category='console',key='ps5-spider-man-2',title='PS5 Spider-Man 2',price=100)
    b=seed(conn,'search-nintendo',category='console',key='unknown',title='Nintendo Switch 2',price=200)
    html=client.get('/?brand=sony&console=PlayStation+5&q=spider&sort=price&direction=asc').text
    assert row_html(html,a) and row_html(html,b) is None


def test_discord_setup_validates_urls_never_echoes_secret_and_connects_without_restart(client,cfg,conn,monkeypatch):
    from dealfinder import connections
    saved=[]
    def save(config,url):
        saved.append(url)
        config.discord_webhook_url=url or None
    monkeypatch.setattr(connections,'save_discord',save)
    assert client.post('/connections/discord',data={'webhook_url':'http://127.0.0.1/admin'}).status_code == 400
    assert not saved
    url='https://discord.com/api/webhooks/123456789012345678/'+'test-secret-token-'*3
    response=client.post('/connections/discord',data={'webhook_url':url})
    assert response.status_code == 200 and 'connected' in response.text.lower()
    assert cfg.discord_webhook_url == url
    assert 'test-secret-token' not in response.text+client.get('/connections').text
    assert all('test-secret-token' not in r[0] for r in conn.execute('SELECT value FROM settings'))
    assert client.post('/connections/discord/disconnect').status_code == 200
    assert cfg.discord_webhook_url is None


def test_browser_site_rotation_and_saved_alert_preferences(client,cfg,conn):
    from dealfinder.connections import load_preferences
    headers={'X-Dealfinder-Token':client.app.state.browser_token}
    assert client.post('/browser/next',json={'sites':['ebay','hibid']}).status_code == 403
    jobs=[client.post('/browser/next',json={'sites':['ebay','hibid']},headers=headers).json() for _ in range(4)]
    assert [j['source'] for j in jobs] == ['ebay','hibid','ebay','hibid']
    assert client.post('/connections/discord/preferences',data={'resale_deal':'on','target_hit':'on'}).status_code == 200
    restarted=Config(db_path=cfg.db_path)
    load_preferences(restarted)
    assert restarted.notify_reasons['resale_deal'] and restarted.notify_reasons['target_hit']
    assert not restarted.notify_reasons['game_lot_deal']


@pytest.mark.parametrize('path,kind,key,condition', [
    ('/lots/hardware','hardware_lot','ps2-console','for_parts'),
])
def test_shared_filters_apply_to_lot_pages_and_partial_refreshes(client,conn,path,kind,key,condition):
    client = AllListingsClient(client)
    a=seed_lot(conn,'lot-search-a',kind=kind,key=key,title='Sony PlayStation 2 bundle',price=50)
    b=seed_lot(conn,'lot-search-b',kind=kind,key=key,title='Sony PlayStation 2 bundle',price=200)
    for headers in ({},{'HX-Request':'true'}):
        response=client.get(path,params={'q':'bundle','brand':'Sony','console':'PlayStation 2','min_price':'100',
                                        'sort':'price','direction':'asc','condition':condition},headers=headers)
        assert response.status_code == 200
        assert row_html(response.text,a) is None and row_html(response.text,b)

def test_ebay_helper_token_is_required_and_cannot_authorize_other_routes(client):
    assert client.post('/browser/ebay/next').status_code == 403
    assert client.post('/browser/ebay/next', headers={'X-Dealfinder-Token':'wrong'}).status_code == 403
    token = client.app.state.browser_token
    headers = {'X-Dealfinder-Token':token, 'Origin':'chrome-extension://test'}
    job = client.post('/browser/ebay/next', headers=headers)
    assert job.status_code == 200 and job.json()['url'].startswith('https://www.ebay.com/sch/')
    assert client.post('/controls/pause', data={'on':'true'}, headers=headers).status_code == 403
    assert client.post('/browser/ebay/next', headers={**headers,'Host':'evil.test:8000'}).status_code == 403


def test_ebay_helper_obeys_pause_and_api_mode(client, cfg, conn):
    headers = {'X-Dealfinder-Token':client.app.state.browser_token}
    Budget(conn, cfg, local_now).set_paused(True)
    assert client.post('/browser/ebay/next', headers=headers).json()['paused']
    Budget(conn, cfg, local_now).set_paused(False)
    cfg.ebay_client_id = 'id'
    cfg.ebay_client_secret = 'secret'
    assert client.post('/browser/ebay/next', headers=headers).json()['paused']


def test_ebay_helper_captures_visible_cards_without_paid_scans(client, fake, conn):
    headers = {'X-Dealfinder-Token':client.app.state.browser_token}
    html = '<li class="s-card"><a href="https://www.ebay.com/itm/366757299846"></a><div class="s-card__title">RTX 4070</div><div class="s-card__price">$625.00</div><div>Buy It Now</div></li>'
    payload = {'html':html, 'query':'rtx 4070', 'captured_at':now_utc().isoformat()}
    result = client.post('/browser/ebay/results', json=payload, headers=headers)
    assert result.status_code == 200 and result.json()['usable'] == 1
    assert fake.imported[0].source == 'ebay' and fake.imported[0].price == 625
    assert not fake.scanned
    assert db.latest_runs(conn)[0]['status'] == 'ok'
    payload['captured_at'] = (now_utc() - timedelta(minutes=10)).isoformat()
    assert client.post('/browser/ebay/results', json=payload, headers=headers).status_code == 400
    assert len(fake.imported) == 1


def test_ebay_helper_error_is_visible_and_escaped(client):
    headers = {'X-Dealfinder-Token':client.app.state.browser_token}
    result = client.post('/browser/ebay/results', headers=headers,
                         json={'error':'Verification <needed>', 'captured_at':now_utc().isoformat()})
    assert result.status_code == 200
    assert 'Verification &lt;needed&gt;' in client.get('/header/status').text
    assert 'Verification &lt;needed&gt;' in client.get('/sources/ebay').text


def test_ebay_helper_download_contains_scoped_extension(client):
    import io
    import zipfile
    response = client.get('/sources/ebay/helper.zip')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['manifest_version'] == 3
        assert set(manifest['permissions']) == {'storage','alarms'}
        assert manifest['host_permissions'] == ['https://www.ebay.com/*', 'https://hibid.com/*', 'http://127.0.0.1:8000/*']
        assert 'capture-hibid.js' in archive.namelist()
        assert {'background.js','capture.js','connect.js'} <= set(archive.namelist())
    setup = client.get('/sources/ebay')
    assert setup.headers['Cache-Control'] == 'no-store'
    assert 'Connect and start' in setup.text


def test_unhandled_refresh_failure_is_visible(cfg, fake):
    def fail():
        raise RuntimeError('Unexpected <failure>')
    fake.refresh = fail
    app = create_app(cfg, build_pipeline=lambda c: fake, start_scheduler=False)
    with HtmxClient(app) as c:
        c.post('/refresh')
        app.state.runner.thread.join(5)
        status = c.get('/header/status').text
        assert 'Refresh failed' in status and '&lt;failure&gt;' in status

@pytest.mark.parametrize('price,profit,tone,label', [(580,200,'good','Good deal'),(800,10,'average','Average'),(880,-20,'overpriced','At limit')])
def test_sold_average_and_deal_colors_render_next_to_current_price(client, conn, price, profit, tone, label):
    client = AllListingsClient(client)
    lid=seed(conn,'rating-'+tone,price=price,profit=profit)
    mv=MarketValue('rtx-4090','used',1000,8,'sold_average',False,now_utc())
    db.save_market_value(conn,mv,[])
    html=client.get('/').text
    row=row_html(html,lid)
    assert f'class="deal-{tone}"' in row
    assert label in row and '$1,000.00' in row and 'sold average' in row
    assert 'Current price' in html and 'Average eBay sold' in html
    detail=client.get(f'/listing/{lid}').text
    assert label in detail and 'id="sold-comps"' in detail


def test_stale_and_asking_values_do_not_color_listings(client, conn):
    client = AllListingsClient(client)
    lid=seed(conn,'rating-unknown',price=100,profit=500)
    for method,at in [('active_bin',now_utc()),('sold',now_utc()),('sold_average',now_utc()-timedelta(days=2))]:
        db.save_market_value(conn,MarketValue('rtx-4090','used',1000,8,method,False,at),[])
        row=row_html(client.get('/').text,lid)
        assert 'class="deal-unknown"' in row
        assert 'rating-good' not in row and 'below sold average' not in row


def test_helper_alternates_sold_evidence_without_skipping_active_catalog_queries(client, conn):
    seed(conn,'needs-sold-evidence')
    headers={'X-Dealfinder-Token':client.app.state.browser_token}
    jobs=[client.post('/browser/ebay/next',headers=headers).json() for _ in range(4)]
    assert [job['kind'] for job in jobs]==['active','sold','active','sold']
    assert jobs[0]['query'] == jobs[2]['query']
    assert 'LH_Auction=1' in jobs[2]['url'] and 'LH_Auction' not in jobs[0]['url']
    assert 'LH_Sold=1' in jobs[1]['url'] and jobs[1]['query']=='rtx 4090'


def test_sold_helper_capture_saves_comps_and_starts_repricing(client, fake, conn):
    at=now_utc()
    day=(at-timedelta(days=1)).strftime('%b %d, %Y')
    html=f'<li class="s-card"><a class="s-card__link" href="https://www.ebay.com/itm/123456789123"></a><div class="s-card__title">RTX 4090</div><div class="s-card__price">$900.00</div><div class="s-card__caption">Sold {day}</div><div class="s-card__subtitle">Pre-Owned</div></li>'
    headers={'X-Dealfinder-Token':client.app.state.browser_token}
    response=client.post('/browser/ebay/results',headers=headers,json={
        'kind':'sold','query':'rtx 4090','html':html,'captured_at':at.isoformat()})
    assert response.status_code==200 and response.json()['usable']==1
    assert fake.started.wait(5)
    client.app.state.runner.thread.join(5)
    assert fake.imported==[]  # sold comps are evidence, not live offers
    from dealfinder.sources.ebay_sold_browser import snapshots
    assert snapshots(conn)['rtx 4090']['comps'][0]['price']==900

@pytest.mark.parametrize('path', ['/', '/lots/hardware', '/lots/games'])
def test_profit_first_default_has_clear_opt_in_without_target_or_scan_controls(client,path):
    html=client.get(path).text
    assert 'Include unpriced and lower-margin listings' in html
    assert 'Target hits only' not in html and 'Photo finds only' not in html
    assert 'Photo scanning:' not in html


def test_cached_accessory_never_reappears_even_in_all_results(client,conn):
    lid=seed(conn,'old-bad-accessory',key='ps5-pro-30th-anniversary',category='console',
             title='PS5 PRO SLIM 30th Anniversary RGB Cooling Fan For PlayStation 5')
    assert row_html(client.get('/?show_all=true').text,lid) is None


def test_idle_status_does_not_trigger_repeated_listing_replacement(client,conn):
    first=client.get('/header/status')
    node=LexborHTMLParser(first.text).css_first('#header-status')
    url=node.attributes['hx-get']
    assert client.get(url).headers.get('HX-Trigger') is None
    seed(conn,'new-capture')
    assert client.get(url).headers.get('HX-Trigger') == 'listings-updated'


def test_catalog_item_alert_selection_persists_independent_of_search(client,conn):
    from dealfinder.subscriptions import subscriptions
    response=client.post('/catalog/product/xbox-360-halo-3-special-edition',data={'enabled':'true','notify_item':'true'})
    assert response.status_code == 200
    assert 'product:xbox-360-halo-3-special-edition' in subscriptions(conn)
    assert 'name="notify_item"' in response.text and 'checked' in response.text
    assert client.post('/connections/discord/preferences',data={'resale_deal':'on','item_scope':'selected'}).status_code == 200
    page=client.get('/connections').text
    assert '1 selected' in page and 'value="selected" selected' in page
    client.post('/catalog/product/xbox-360-halo-3-special-edition',data={'enabled':'true'})
    assert not subscriptions(conn)



def test_search_requests_queue_both_formats_once_and_keep_regular_rotation(client,conn):
    client.get('/?q=Halo+3+Xbox+360&show_all=true')
    queue=json.loads(db.get_setting(conn,'ebay_requested_searches'))
    assert [job['auction'] for job in queue] == [False,True]
    client.get('/?q=Halo+3+Xbox+360&show_all=true',headers=HTMX)
    assert json.loads(db.get_setting(conn,'ebay_requested_searches')) == queue
    headers={'X-Dealfinder-Token':client.app.state.browser_token}
    jobs=[client.post('/browser/ebay/next',headers=headers).json() for _ in range(3)]
    assert jobs[0]['query']==jobs[1]['query']=='Halo 3 Xbox 360'
    assert 'LH_Auction=1' in jobs[1]['url']
    assert jobs[2]['query'] != jobs[1]['query']


def test_default_view_uses_current_sold_value_instead_of_stale_stored_profit(client,conn):
    lid=seed(conn,'cached-false-profit',price=850,profit=500)
    db.save_market_value(conn,MarketValue('rtx-4090','used',900,8,'sold_average',False,now_utc()),[])
    assert row_html(client.get('/').text,lid) is None
    row=row_html(client.get('/?show_all=true').text,lid)
    assert 'deal-overpriced' in row


@pytest.mark.parametrize("count,has_next", [(240,False),(60,True)])
def test_full_ebay_pages_queue_a_bounded_next_page(client,conn,count,has_next):
    from tests.test_ebay_browser import card
    headers={'X-Dealfinder-Token':client.app.state.browser_token}
    html=''.join(card(item=str(900000000000+i)) for i in range(count))
    payload={'html':html,'has_next':has_next,'query':'halo 3 xbox 360','page':1,'auction':True,'captured_at':now_utc().isoformat()}
    assert client.post('/browser/ebay/results',json=payload,headers=headers).status_code == 200
    queue=json.loads(db.get_setting(conn,'ebay_requested_searches'))
    assert queue[0] == {'query':'halo 3 xbox 360','page':2,'auction':True}
    db.set_setting(conn,'ebay_requested_searches','[]')
    payload['page']=5
    client.post('/browser/ebay/results',json=payload,headers=headers)
    assert json.loads(db.get_setting(conn,'ebay_requested_searches')) == []


def test_large_result_sets_are_paginated_without_losing_matches(client,conn):
    ids=[seed(conn,f'page-{i}') for i in range(101)]
    first=client.get('/?show_all=true').text
    assert row_html(first,ids[0]) and row_html(first,ids[-1]) is None
    second=client.get('/?show_all=true&page=2').text
    assert row_html(second,ids[-1]) and row_html(second,ids[0]) is None
    assert 'Page 2 of 2' in second and '101 matching active listings' in second

@pytest.mark.parametrize('title,key', [
 ('ASUS ROG XG Mobile eGPU Dock RTX 4090 16GB GDDR6 (GC33Y-059)','rtx-4090'),
 ('Vintage Vivitar PZ 3090 35mm Point-and-Shoot Film Camera','rtx-3090'),
 ('HP Omen 17t-ck200 RTX 4090 i7-13700HX 17 inch','rtx-4090'),
])
def test_wrong_product_types_are_hidden_from_saved_results_and_invalidate_values(client,conn,title,key):
    lid=seed(conn,'wrong-'+key,key=key,title=title,profit=9999)
    db.save_market_value(conn,MarketValue(key,'used',3000,9,'sold_average',False,now_utc()),
                         [Comp(title,3000,None,'https://www.ebay.com/itm/123456789012',None)])
    assert row_html(client.get('/?show_all=true').text,lid) is None
    assert db.get_market_value(conn,key,'used') is None


def test_game_lot_feature_is_retired_from_navigation_catalog_alerts_and_ingestion(client,cfg,conn):
    from dataclasses import replace
    from dealfinder.catalog import load_catalog
    for path in ('/','/connections','/catalog'):
        html=client.get(path).text
        assert 'href="/lots/games"' not in html and 'Game lot deals' not in html
        assert 'Game platforms' not in html
    response=client.get('/lots/games',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/'
    assert client.post('/catalog/platform/ps2',data={'enabled':'true'}).status_code==410
    db.set_override(conn,'platform_overrides','ps2',enabled=True)
    assert not load_catalog(cfg.catalog_dir,conn,cfg).enabled_platforms()
    factory=default_pipeline_factory(cfg)
    try:
        pipeline=factory(conn)
        listing=Listing('ebay','retired-games','Lot of 20 PS2 games','https://www.ebay.com/itm/123456789012',10,0,True,[],False,None,observed_at=now_utc())
        assert pipeline.import_listings([listing])==0
        assert conn.execute("SELECT COUNT(*) FROM listings WHERE source_id='retired-games'").fetchone()[0]==0
    finally:
        factory.close()


@pytest.mark.parametrize('title',[
    'Killscreen 1080-R Gray PS5 Pro Controller Retro PlayStation 5 30th Anniversary',
    'BOX & MANUALS! SPIDER-MAN 2 LIMITED EDITION PLAYSTATION 5 PS5 CONSOLE BOX & MAN!',
    'Nintendo Switch OLED Display Zelda Tears Of The Kingdom Cracked Display',
    "Spider-Man 2 Collector's Limited Edition For Sony Playstation 5 PS5 Spiderman",
])
def test_cached_non_console_items_cannot_use_console_comparisons(client,conn,title):
    key='ps5-30th-anniversary'
    lid=seed(conn,'wrong-console',key=key,category='console',title=title,price=100,profit=999)
    db.save_market_value(conn,MarketValue(key,'used',1200,8,'sold_average',False,now_utc()),
                         [Comp(title,1200,None,'https://www.ebay.com/itm/123456789012',None)])
    assert row_html(client.get('/?show_all=true').text,lid) is None
    assert db.get_market_value(conn,key,'used') is None


def test_background_scanner_stops_extension_jobs_but_accepts_builtin_collector(client):
    client.app.state.background_enabled = True
    headers = {'X-Dealfinder-Token': client.app.state.browser_token}
    for path in ['/browser/next','/browser/ebay/next','/browser/hibid/next']:
        response=client.post(path,headers=headers,json={'sites':['ebay','hibid']})
        assert response.status_code == 200 and response.json()['paused']
    assert client.post('/browser/ebay/results',headers=headers,json={}).status_code == 409
    assert client.post('/browser/next',headers={**headers,'X-ConsoleHawk-Collector':'background'},json={'sites':['ebay']}).json()['source']=='ebay'
    assert client.post('/browser/next',headers={'X-ConsoleHawk-Collector':'background'},json={'sites':['ebay']}).status_code == 403


def test_background_controls_validate_commands_and_keep_session_data_private(client):
    client.app.state.background_enabled = True
    page=client.get('/connections')
    assert 'Background scanning' in page.text
    assert 'Connect both sites' not in page.text
    assert 'data-helper-token' not in page.text
    assert client.post('/connections/scanner/ebay/signin').status_code == 200
    assert client.app.state.collector.next_command() == ('signin','ebay')
    assert client.post('/connections/scanner/hibid/resume').status_code == 200
    assert client.app.state.collector.next_command() == ('resume','hibid')
    assert client.post('/connections/scanner/unknown/signin').status_code == 400
    assert client.post('/connections/scanner/ebay/execute').status_code == 400
    assert client.app.state.browser_token not in client.get('/connections/scanner').text
    assert client.app.state.browser_token not in page.text


def test_public_only_collection_skips_sold_jobs_and_keeps_pagination(client, conn, monkeypatch):
    from dealfinder.sources import ebay_sold_browser
    monkeypatch.setattr(ebay_sold_browser, 'needed_queries', lambda *args: ['ps2'])
    db.set_setting(conn, 'ebay_browser_rotation', '1')
    db.set_setting(conn, 'ebay_requested_searches', '[{"query":"halo 3","auction":true,"page":2}]')
    response = client.post('/browser/next', json={'sites':['ebay'], 'allow_sold':False},
        headers={'X-Dealfinder-Token':client.app.state.browser_token})
    assert response.status_code == 200
    job = response.json()
    assert job['kind'] == 'active'
    assert '_pgn=2' in job['url'] and 'LH_Auction=1' in job['url']
