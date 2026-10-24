from datetime import datetime, timezone

from dealfinder.config import Config
from dealfinder.finds import FindEval, evaluate_finds
from dealfinder.models import MarketValue, ScanItem, ScanResult

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def item(title, confidence="high", quantity=1):
    return ScanItem(kind="game", platform="Nintendo 64", title=title, edition=None,
                    completeness="loose", quantity=quantity, confidence=confidence,
                    photo_index=0)


def price(value, low_confidence=False):
    return MarketValue(key="item", condition="loose", value=value, comp_count=5,
                       method="sold", low_confidence=low_confidence, computed_at=NOW)


def result(items):
    return ScanResult(status="ok", items=items, unit_count_estimate=None, notes="",
                      model="claude-opus-5-5", input_tokens=0, output_tokens=0, cost_usd=0.0)


def evaluate(pairs, total_cost=500.0):
    """pairs: (ScanItem, MarketValue | None); prices are looked up by item identity."""
    by_id = {id(i): p for i, p in pairs}
    items = [i for i, _ in pairs]
    return evaluate_finds(result(items), lambda i: by_id[id(i)], total_cost, Config())


def test_rare_find():
    zelda = item("Zelda")
    found = evaluate([(zelda, price(60))])
    assert found == FindEval(identified_value=60, is_rare_find=True, is_lot_find=False,
                             rare_items=[(zelda, 60)], priced=[(zelda, price(60))])


def test_below_rare_threshold_not_rare():
    found = evaluate([(item("Mario"), price(49.99))])
    assert found.is_rare_find is False
    assert found.rare_items == []
    assert found.identified_value == 49.99


def test_low_confidence_item_ignored():
    guess = item("Maybe Earthbound", confidence="low")
    found = evaluate([(guess, price(300))])
    assert found.identified_value == 0
    assert found.is_rare_find is False
    assert found.priced == [(guess, price(300))]  # still shown


def test_low_confidence_price_ignored():
    thin = item("Obscure", confidence="medium")
    unpriced = item("Unknown")
    found = evaluate([(thin, price(300, low_confidence=True)), (unpriced, None)])
    assert found.identified_value == 0
    assert found.is_rare_find is False
    assert found.priced == [(thin, price(300, low_confidence=True)), (unpriced, None)]


def test_value_multiplies_quantity_and_rare_uses_unit_price():
    pads = item("Controller", confidence="medium", quantity=4)
    found = evaluate([(pads, price(20))])
    assert found.identified_value == 80
    assert found.is_rare_find is False


def test_lot_find_threshold():
    games = item("Common game", quantity=5)
    assert evaluate([(games, price(19))], total_cost=40).is_lot_find is True  # 95 >= 60 and >= 90
    assert evaluate([(games, price(17))], total_cost=40).is_lot_find is False  # 85 < 90


def test_lot_find_needs_one_and_a_half_times_cost():
    games = item("Common game", quantity=10)
    # 250 >= 200 + 50 but < 1.5 * 200
    assert evaluate([(games, price(25))], total_cost=200).is_lot_find is False
    assert evaluate([(games, price(30))], total_cost=200).is_lot_find is True
