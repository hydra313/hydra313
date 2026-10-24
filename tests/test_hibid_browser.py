from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dealfinder import db
from dealfinder.config import Config
from dealfinder.sources.base import SourceNeedsAttention
from dealfinder.sources.hibid_browser import (
    HiBidBrowserSource, parse_browser_page, save_snapshot, snapshot_status,
)

NOW = datetime(2026, 10, 23, 22, tzinfo=timezone.utc)
HTML = (Path(__file__).parent / "fixtures/hibid/browser_search.html").read_text(encoding="utf-8")


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "browser.db")
    yield connection
    connection.close()


def test_parses_real_browser_cards_with_currency_and_shipping_guards():
    result = parse_browser_page(HTML, NOW)
    assert result.cards == 5
    assert len(result.listings) == 2
    gpu = result.listings[0]
    assert gpu.title == "Gigabyte GeForce RTX 5070 Ti WINDFORCE OC SFF 16G"
    assert gpu.price == 625
    assert gpu.bid_count == 21
    assert gpu.shipping is None and gpu.shipping_offered
    assert gpu.end_time == NOW + timedelta(hours=20, minutes=27)
    assert gpu.observed_at == NOW
    assert gpu.url.startswith("https://hibid.com/lot/")
    assert gpu.image_urls[0].startswith("https://cdn.hibid.com/")
    assert gpu.buyer_premium_pct is None
    assert result.listings[1].price > 0  # no-bid $0 card uses the next bid, not free
    assert sum(result.skipped.values()) == 3


def test_copied_fragment_without_card_wrapper_is_supported():
    html = ('<a href="https://hibid.com/lot/123/gpu">RTX 4070</a>'
            '<p>1d 2h 3m</p><p>$40.00 USD</p><p>2 Bids</p>'
            '<p>Shipping Available</p><a href="https://hibid.com/pf/auction/x">Bid 45.00 USD</a>')
    result = parse_browser_page(html, NOW)
    assert [(l.source_id, l.price) for l in result.listings] == [("123", 40)]


@pytest.mark.parametrize("html", ["", "<html>Attention Required! Cloudflare</html>",
                                  "<html>Loading...</html>", "<p>plain copied text</p>"])
def test_non_result_pages_explain_browser_attention(html):
    with pytest.raises(ValueError, match="browser|HiBid|results|HTML"):
        parse_browser_page(html, NOW)


def test_invalid_links_currency_and_countdowns_are_not_guessed():
    base = ('<div class="app-auction-lot-card"><a class="app-auction-lot-card__title" '
            'href="{url}">RTX 4070</a><span class="app-auction-lot-badge__value">{end}</span>'
            '<div class="app-auction-lot-card__meta-money">{money} 2 Bids</div>'
            '<span class="app-auction-lot-card__shipping">Shipping Available</span></div>')
    cards = [base.format(url=url, end=end, money=money) for url, end, money in [
        ("https://evil.example/lot/123", "2h", "$40.00 USD"),
        ("/lot/123/gpu", "Webcast", "$40.00 USD"),
        ("/lot/123/gpu", "2h", "$40.00 CAD"),
        ("/lot/123/gpu", "2h", "$40.00"),
    ]]
    result = parse_browser_page("".join(cards), NOW)
    assert result.cards == 4 and result.listings == []


def test_source_needs_import_then_reads_only_fresh_snapshots(conn):
    cfg = Config()
    source = HiBidBrowserSource(conn, cfg, lambda: NOW)
    with pytest.raises(SourceNeedsAttention, match="import"):
        source.search("")
    parsed = parse_browser_page(HTML, NOW)
    save_snapshot(conn, "rtx", parsed, NOW, 1)
    assert len(source.search("")) == 2
    assert snapshot_status(conn, cfg, NOW)["state"] == "ready"
    expired = HiBidBrowserSource(conn, cfg, lambda: NOW + timedelta(minutes=31))
    with pytest.raises(SourceNeedsAttention, match="expired"):
        expired.search("")


def test_multiple_queries_dedupe_by_newest_capture_without_renewing_time(conn):
    old = parse_browser_page(HTML, NOW - timedelta(minutes=10))
    save_snapshot(conn, "rtx", old, NOW - timedelta(minutes=10), 1)
    new = parse_browser_page(HTML.replace("625.00", "650.00"), NOW)
    save_snapshot(conn, "geforce", new, NOW, 1)
    listings = HiBidBrowserSource(conn, Config(), lambda: NOW).search("")
    assert len(listings) == 2
    assert listings[0].price == 650 and listings[0].observed_at == NOW
    assert snapshot_status(conn, Config(), NOW)["queries"] == 2


def test_raw_page_is_not_stored_and_images_cannot_target_localhost(conn):
    parsed = parse_browser_page(HTML.replace("https://cdn.hibid.com/", "http://127.0.0.1/"), NOW)
    assert all(not l.image_urls for l in parsed.listings)
    save_snapshot(conn, "rtx", parsed, NOW, 1)
    values = " ".join(r[0] for r in conn.execute("SELECT value FROM settings"))
    assert "<div" not in values and "127.0.0.1" not in values
