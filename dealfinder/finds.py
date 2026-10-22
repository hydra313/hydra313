from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dealfinder.config import Config
from dealfinder.models import MarketValue, ScanItem, ScanResult

_COUNTED_CONFIDENCE = ("high", "medium")


@dataclass
class FindEval:
    identified_value: float
    is_rare_find: bool
    is_lot_find: bool
    rare_items: list[tuple[ScanItem, float]]
    priced: list[tuple[ScanItem, MarketValue | None]]


def evaluate_finds(
    result: ScanResult,
    price_of: Callable[[ScanItem], MarketValue | None],
    total_cost: float,
    cfg: Config,
) -> FindEval:
    """Spec 11.6: every item is priced (for display); only high/medium-confidence
    items with a confident price count toward the rare-find and lot-find alerts."""
    priced = [(item, price_of(item)) for item in result.items]
    identified_value = 0.0
    rare_items: list[tuple[ScanItem, float]] = []
    for item, price in priced:
        if item.confidence not in _COUNTED_CONFIDENCE:
            continue
        if price is None or price.low_confidence:
            continue
        identified_value += price.value * item.quantity
        if price.value >= cfg.rare_item_threshold:
            rare_items.append((item, price.value))
    is_lot_find = (
        identified_value >= 1.5 * total_cost
        and identified_value >= total_cost + cfg.min_lot_profit
    )
    return FindEval(
        identified_value=identified_value,
        is_rare_find=bool(rare_items),
        is_lot_find=is_lot_find,
        rare_items=rare_items,
        priced=priced,
    )
