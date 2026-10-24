from datetime import datetime, timedelta, timezone

import pytest

from dealfinder.config import Config
from dealfinder.deal_rating import compare_deal
from dealfinder.models import MarketValue

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def value(**changes):
    values = dict(key='gpu', condition='used', value=1000, comp_count=8,
                  method='sold_average', low_confidence=False, computed_at=NOW)
    return MarketValue(**{**values, **changes})


@pytest.mark.parametrize('cost,profit,expected', [(600,200,'good'), (850,10,'average'),
                                                 (900,-20,'overpriced'), (1000,-100,'overpriced'),
                                                 (800,0,'overpriced')])
def test_colors_include_fees_and_profit_not_only_discount(cost, profit, expected):
    result = compare_deal(value(), cost, profit, 1, Config(), NOW)
    assert result['tone'] == expected
    assert result['discount'] == pytest.approx(1-cost/1000)
    assert result['average'] == 1000


@pytest.mark.parametrize('mv', [None, value(low_confidence=True), value(comp_count=2),
                              value(method='active_bin'), value(method='sold'),
                              value(computed_at=NOW-timedelta(hours=25)), value(value=0)])
def test_missing_weak_asking_legacy_and_stale_values_never_get_traffic_light(mv):
    result = compare_deal(mv, 100, 500, 1, Config(), NOW)
    assert result['tone'] == 'unknown' and result['discount'] is None


def test_lot_comparison_uses_entire_lot_and_requires_quantity():
    assert compare_deal(value(value=100), 200, 60, 4, Config(), NOW, lot=True)['average'] == 400
    assert compare_deal(value(), 100, 500, None, Config(), NOW, lot=True)['tone'] == 'unknown'
