"""Deal scoring for single items, hardware lots and game lots (spec §8)."""
from __future__ import annotations

from datetime import datetime, timedelta

from dealfinder.config import Config
from dealfinder.models import BulkProduct, Listing, MarketValue, Product, Scored


def total_cost(listing: Listing, cfg: Config, est_shipping: float) -> tuple[float, bool]:
    """Landed cost and whether the shipping part is an estimate."""
    premium = (listing.buyer_premium_pct if listing.buyer_premium_pct is not None
               else cfg.buyer_premium_pct[listing.source])
    estimated = listing.shipping is None
    shipping = est_shipping if estimated else listing.shipping
    cost = (listing.price + shipping + listing.price * premium
            + cfg.handling_fee[listing.source] + listing.price * cfg.tax_rate)
    return cost, estimated


def time_weight(listing: Listing, now: datetime) -> float:
    if not listing.is_auction:
        return 1.0
    if listing.end_time is None:
        return 0.4
    remaining = listing.end_time - now
    if remaining <= timedelta(hours=12):
        return 1.0
    if remaining <= timedelta(hours=48):
        return 0.7
    return 0.4


def _rank(profit: float | None, listing: Listing, now: datetime) -> float:
    return max(profit or 0, 0) * time_weight(listing, now)


def score_single(listing: Listing, product: Product, value: MarketValue | None,
                 cfg: Config, now: datetime) -> Scored:
    cost, estimated = total_cost(listing, cfg, cfg.buy_shipping_estimate[product.category])
    target_hit = product.max_price is not None and cost <= product.max_price
    profit = discount = None
    resale_deal = False
    if value is not None:
        resale_net = value.value * (1 - cfg.sell_fee_rate) - cfg.sell_shipping[product.category]
        profit = resale_net - cost
        discount = 1 - cost / value.value
        resale_deal = profit >= cfg.min_profit and discount >= cfg.min_discount
    return Scored(total_cost=cost, shipping_estimated=estimated, profit=profit,
                  discount_pct=discount, is_target_hit=target_hit,
                  is_deal=target_hit or resale_deal, rank_score=_rank(profit, listing, now))


def score_hardware_lot(listing: Listing, bulk: BulkProduct, qty: int,
                       unit_value: MarketValue | None, cfg: Config, now: datetime) -> Scored:
    cost, estimated = total_cost(
        listing, cfg, cfg.buy_shipping_estimate[bulk.category] * qty)
    profit = discount = None
    deal = False
    if unit_value is not None:
        unit_net = unit_value.value * (1 - cfg.sell_fee_rate) - cfg.sell_shipping[bulk.category]
        revenue = qty * bulk.success_rate * unit_net
        profit = revenue - qty * bulk.repair_cost - cost
        discount = 1 - cost / (qty * unit_value.value)
        deal = profit >= cfg.min_lot_profit
    return Scored(total_cost=cost, shipping_estimated=estimated, profit=profit,
                  discount_pct=discount, is_target_hit=False, is_deal=deal,
                  rank_score=_rank(profit, listing, now))


def score_game_lot(listing: Listing, qty: int, per_game: MarketValue | None,
                   cfg: Config, now: datetime) -> Scored:
    lot_shipping = cfg.lot_ship_base + cfg.lot_ship_per_game * qty
    cost, estimated = total_cost(listing, cfg, lot_shipping)
    profit = discount = None
    deal = False
    if per_game is not None:
        gross = qty * per_game.value
        profit = gross * (1 - cfg.sell_fee_rate) - lot_shipping - cost
        discount = 1 - cost / gross
        deal = profit >= cfg.min_lot_profit and discount >= cfg.min_discount
    return Scored(total_cost=cost, shipping_estimated=estimated, profit=profit,
                  discount_pct=discount, is_target_hit=False, is_deal=deal,
                  rank_score=_rank(profit, listing, now))
