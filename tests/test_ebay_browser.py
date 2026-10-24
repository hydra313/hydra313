from datetime import datetime, timedelta, timezone

import pytest

from dealfinder import db
from dealfinder.config import Config
from dealfinder.models import Classified
from dealfinder.sources.ebay_browser import EbayBrowserSource, parse_active_page, search_link
from dealfinder.sources.base import SourceNeedsAttention

NOW = datetime(2026, 10, 23, 21, tzinfo=timezone.utc)


def card(item="366757299846", title="RTX 4070 Founders Edition", price="$625.00",
         shipping="Free delivery", buying="Buy It Now", condition="Open Box", host="www.ebay.com"):
    return f'''<li class="s-card"><a href="https://{host}/itm/{item}?tracking=discard">
      <div class="s-card__title">NEW LISTING{title} Opens in a new window or tab</div></a>
      <div class="s-card__subtitle">NVIDIA · 12 GB</div>
      <div class="s-card__subtitle">{condition}</div>
      <div class="s-card__price">{price}</div>
      <div class="s-card__attribute-row">{buying}</div>
      <div class="s-card__attribute-row">{shipping}</div>
      <img src="https://i.ebayimg.com/images/example.webp"></li>'''


def test_visible_active_cards_use_usd_price_condition_and_canonical_identity():
    page = parse_active_page(card() + card(), NOW)
    assert page.cards == 2 and len(page.listings) == 1
    item = page.listings[0]
    assert item.title == "RTX 4070 Founders Edition"
    assert item.source_id == "v1|366757299846|0"
    assert item.url == "https://www.ebay.com/itm/366757299846"
    assert item.price == 625 and item.shipping == 0
    assert item.condition_id == "3000"  # Open Box is conservatively used
    assert item.observed_at == NOW and not item.is_auction


@pytest.mark.parametrize("kwargs", [
    {"price":"C $625.00"}, {"price":"$500.00 to $625.00"},
    {"price":"$nan"}, {"price":"$0.00"}, {"host":"ebay.com.evil.test"},
    {"buying":"4 bids"}, {"shipping":"Local pickup only"},
    {"title":"Shop on eBay"}, {"buying":"Sold Oct 23, 2026"},
])
def test_ambiguous_prices_closed_auctions_pickup_and_parts_are_not_single_items(kwargs):
    assert parse_active_page(card(**kwargs), NOW).listings == []


def test_paid_shipping_and_unknown_shipping_are_distinct():
    assert parse_active_page(card(shipping="+$12.50 delivery"), NOW).listings[0].shipping == 12.5
    assert parse_active_page(card(shipping="Shipping calculated at checkout"), NOW).listings[0].shipping is None


def test_parts_condition_reaches_classifier_even_when_omitted_from_title():
    item = parse_active_page(card(condition="For parts or not working"), NOW).listings[0]
    assert item.condition_id == "7000" and "for parts" in item.title


def test_fixed_price_best_offer_cards_are_supported_but_bid_prices_are_not():
    assert len(parse_active_page(card(buying="or Best Offer"), NOW).listings) == 1
    assert parse_active_page(card(buying="4 bids or Best Offer"), NOW).listings == []


def test_empty_search_is_not_confused_with_verification_or_layout_failure():
    assert parse_active_page('<h1>0 results for rare console</h1>', NOW).cards == 0
    with pytest.raises(ValueError, match="verification|results"):
        parse_active_page('<h1>Pardon our interruption</h1>', NOW)


def test_search_link_is_fixed_price_newest_first():
    url = search_link('rtx 4070 & gpu')
    assert "LH_BIN=1" in url and "_sop=10" in url and "rtx+4070+%26+gpu" in url


def test_browser_source_never_renews_capture_time(tmp_path):
    conn = db.connect(tmp_path / 'browser.db')
    cfg = Config()
    item = parse_active_page(card(), NOW).listings[0]
    db.upsert_listing(conn, item, Classified('single', 'rtx-4070', 'used', 1, 'gpu'), None, NOW)
    source = EbayBrowserSource(conn, cfg, lambda: NOW + timedelta(minutes=20))
    assert source.search('')[0].observed_at == NOW
    expired = EbayBrowserSource(conn, cfg, lambda: NOW + timedelta(hours=7))
    with pytest.raises(SourceNeedsAttention):
        expired.search('')
    conn.close()
