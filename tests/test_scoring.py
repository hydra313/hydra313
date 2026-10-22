from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dealfinder.config import Config
from dealfinder.models import BulkProduct, Listing, MarketValue, Product
from dealfinder.scoring import (
    score_game_lot,
    score_hardware_lot,
    score_single,
    time_weight,
    total_cost,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def make_listing(source="shopgoodwill", price=100.0, shipping=0.0, shipping_offered=True,
                 is_auction=False, end_time=None, buyer_premium_pct=None) -> Listing:
    return Listing(
        source=source, source_id="1", title="t", url="u", price=price, shipping=shipping,
        shipping_offered=shipping_offered, image_urls=[], is_auction=is_auction,
        end_time=end_time, buyer_premium_pct=buyer_premium_pct,
    )


def make_value(key="x", value=500.0) -> MarketValue:
    return MarketValue(key=key, condition="used", value=value, comp_count=10,
                       method="sold", low_confidence=False, computed_at=NOW)


def make_product(category="gpu", max_price=None) -> Product:
    return Product(id="p", category=category, name="P", ebay_query="q", match="m",
                   max_price=max_price)


def test_total_cost_hibid_premium():
    listing = make_listing(source="hibid", price=100, shipping=15, buyer_premium_pct=0.18)
    cost, estimated = total_cost(listing, Config(), 20)
    assert cost == pytest.approx(133.0)
    assert estimated is False


def test_total_cost_config_premium_and_tax():
    cfg = Config(tax_rate=0.1)
    listing = make_listing(source="hibid", price=100, shipping=0.0)
    cost, estimated = total_cost(listing, cfg, 20)
    assert cost == pytest.approx(100 + 0 + 15 + 0 + 10)  # free shipping is not estimated
    assert estimated is False


def test_total_cost_estimated_shipping():
    listing = make_listing(price=200, shipping=None)
    cost, estimated = total_cost(listing, Config(), Config().buy_shipping_estimate["gpu"])
    assert cost == pytest.approx(225.0)
    assert estimated is True


def test_single_resale_deal():
    listing = make_listing(source="ebay", price=300)
    s = score_single(listing, make_product(), make_value(value=500), Config(), NOW)
    assert s.total_cost == pytest.approx(300)
    assert s.profit == pytest.approx(113.75)
    assert s.discount_pct == pytest.approx(0.40)
    assert s.is_target_hit is False
    assert s.is_deal is True
    assert s.rank_score == pytest.approx(113.75)


def test_single_target_hit_without_value():
    listing = make_listing(source="ebay", price=340)
    s = score_single(listing, make_product(max_price=350), None, Config(), NOW)
    assert s.is_target_hit is True
    assert s.is_deal is True
    assert s.profit is None
    assert s.discount_pct is None
    assert s.rank_score == 0


def test_single_no_value_no_target_is_not_deal():
    listing = make_listing(source="ebay", price=340)
    s = score_single(listing, make_product(), None, Config(), NOW)
    assert s.is_target_hit is False
    assert s.is_deal is False


def test_single_not_deal_low_discount():
    listing = make_listing(source="ebay", price=420)
    s = score_single(listing, make_product(), make_value(value=500), Config(), NOW)
    assert s.profit == pytest.approx(500 * 0.8675 - 20 - 420)
    assert s.is_deal is False
    assert s.rank_score == 0


def test_single_estimated_shipping_uses_category_estimate():
    listing = make_listing(source="ebay", price=100, shipping=None)
    s = score_single(listing, make_product("console"), None, Config(), NOW)
    assert s.total_cost == pytest.approx(130)
    assert s.shipping_estimated is True


def test_hardware_lot():
    bulk = BulkProduct(id="b", category="console", name="PS2", ebay_query="q", match="m",
                       repair_cost=25, success_rate=0.6)
    listing = make_listing(source="ebay", price=100)
    s = score_hardware_lot(listing, bulk, 5, make_value(value=80), Config(), NOW)
    assert s.profit == pytest.approx(-106.80)
    assert s.is_deal is False
    assert s.is_target_hit is False
    assert s.rank_score == 0


def test_hardware_lot_deal_and_estimated_shipping_scales_with_qty():
    bulk = BulkProduct(id="b", category="console", name="PS2", ebay_query="q", match="m",
                       repair_cost=5, success_rate=1.0)
    listing = make_listing(source="ebay", price=50, shipping=None)
    s = score_hardware_lot(listing, bulk, 4, make_value(value=100), Config(), NOW)
    assert s.total_cost == pytest.approx(50 + 30 * 4)
    assert s.shipping_estimated is True
    unit_net = 100 * 0.8675 - 30
    assert s.profit == pytest.approx(4 * unit_net - 4 * 5 - s.total_cost)


def test_hardware_lot_without_value_is_unscored():
    bulk = BulkProduct(id="b", category="console", name="PS2", ebay_query="q", match="m",
                       repair_cost=25, success_rate=0.6)
    s = score_hardware_lot(make_listing(), bulk, 5, None, Config(), NOW)
    assert s.profit is None
    assert s.is_deal is False


def test_game_lot():
    cfg = Config()
    s = score_game_lot(make_listing(source="ebay", price=40), 20, make_value(value=6.0), cfg, NOW)
    assert s.profit == pytest.approx(48.10)
    assert s.is_deal is False  # profit < 50
    s = score_game_lot(make_listing(source="ebay", price=30), 20, make_value(value=6.0), cfg, NOW)
    assert s.profit == pytest.approx(58.10)
    assert s.discount_pct == pytest.approx(0.75)
    assert s.is_deal is True


def test_game_lot_estimated_shipping():
    listing = make_listing(source="ebay", price=30, shipping=None)
    s = score_game_lot(listing, 20, make_value(value=6.0), Config(), NOW)
    assert s.total_cost == pytest.approx(30 + 8 + 0.4 * 20)
    assert s.shipping_estimated is True


def test_game_lot_without_value_is_unscored():
    s = score_game_lot(make_listing(), 20, None, Config(), NOW)
    assert s.profit is None
    assert s.is_deal is False


def test_time_weight():
    assert time_weight(make_listing(is_auction=False), NOW) == 1.0
    auction = lambda h: make_listing(is_auction=True, end_time=NOW + timedelta(hours=h))
    assert time_weight(auction(6), NOW) == 1.0
    assert time_weight(auction(12), NOW) == 1.0
    assert time_weight(auction(30), NOW) == 0.7
    assert time_weight(auction(48), NOW) == 0.7
    assert time_weight(auction(72), NOW) == 0.4
    assert time_weight(auction(-1), NOW) == 1.0
    assert time_weight(make_listing(is_auction=True, end_time=None), NOW) == 0.4


def test_rank_score_applies_time_weight():
    listing = make_listing(source="ebay", price=300, is_auction=True,
                           end_time=NOW + timedelta(hours=30))
    s = score_single(listing, make_product(), make_value(value=500), Config(), NOW)
    assert s.rank_score == pytest.approx(113.75 * 0.7)
