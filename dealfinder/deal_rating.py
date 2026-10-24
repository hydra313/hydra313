"""Display a conservative deal rating only when recent sold evidence supports it."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from dealfinder.config import Config
from dealfinder.models import MarketValue


def compare_deal(value: MarketValue | None, cost: float | None, profit: float | None,
                 quantity: int | None, cfg: Config, now: datetime, *, lot: bool = False) -> dict:
    result = {"tone": "unknown", "label": "Unknown", "average": None,
              "discount": None, "reason": "No reliable average sold value yet"}
    if value is None or value.method != "sold_average" or not math.isfinite(value.value) or value.value <= 0:
        return result
    if quantity and quantity > 0:
        result["average"] = value.value * quantity
    if (value.low_confidence or value.comp_count < cfg.min_comps
            or now - value.computed_at >= timedelta(hours=cfg.market_ttl_hours)):
        result["reason"] = "Sold evidence is limited or stale; refresh comparisons"
        return result
    if (not quantity or quantity <= 0 or cost is None or profit is None
            or not math.isfinite(cost) or not math.isfinite(profit)):
        result["reason"] = "Quantity or cost estimate is missing"
        return result
    result["discount"] = 1 - cost / result["average"]
    if profit <= 0:
        result.update(tone="overpriced", label="At limit · Pass",
                      reason="At or above estimated break-even value after costs; no profit margin")
    elif (profit >= (cfg.min_lot_profit if lot else cfg.min_profit)
          and result["discount"] >= cfg.min_discount):
        result.update(tone="good", label="Good deal", reason="Meets estimated profit and discount targets")
    else:
        result.update(tone="average", label="Average", reason="Some estimated margin remains, below deal targets")
    return result
