import pytest

from dealfinder.config import load_config


def test_defaults_without_file():
    cfg = load_config(None, env={})
    assert cfg.sell_fee_rate == 0.1325 and cfg.min_lot_profit == 50
    assert cfg.scan_cost_ceiling == 0.50  # one scan including a fallback-model retry
    assert cfg.vision_prices == {"claude-opus-5-5": (4.00, 20.00),
                                 "claude-opus-5": (5.0, 25.0),
                                 "claude-opus-4-8": (5.0, 25.0)}
    assert cfg.sell_shipping == {"gpu": 20, "cpu": 8, "console": 30, "controller": 10}
    assert cfg.repair_defaults["console"] == (25, 0.60)
    assert cfg.notify_reasons["resale_deal"] is False
    assert cfg.anthropic_api_key is None


def test_cache_ttl_and_min_comp_defaults():
    cfg = load_config(None, env={})
    assert cfg.market_ttl_hours == 24
    assert cfg.item_price_ttl_days == 30
    assert cfg.min_comps == 5
    assert cfg.min_item_comps == 3


def test_unknown_yaml_key_raises(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("min_proft: 40\n")
    with pytest.raises(ValueError, match="min_proft"):
        load_config(p, env={})


def test_yaml_overrides_merge_one_level(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("min_profit: 40\nsell_shipping: {gpu: 25}\n")
    cfg = load_config(p, env={})
    assert cfg.min_profit == 40
    assert cfg.sell_shipping == {"gpu": 25, "cpu": 8, "console": 30, "controller": 10}


def test_secrets_from_env():
    cfg = load_config(None, env={"DISCORD_WEBHOOK_URL": "https://d/x", "ANTHROPIC_API_KEY": "k"})
    assert cfg.discord_webhook_url == "https://d/x" and cfg.anthropic_api_key == "k"


def test_broad_queries_include_collectible_consoles():
    cfg = load_config(None, env={})
    for source in ("shopgoodwill", "hibid"):
        for q in ("limited edition console", "special edition console", "halo xbox 360"):
            assert q in cfg.broad_queries[source]

@pytest.mark.parametrize('value', [0, -1, 1.5, True, 1441])
def test_refresh_interval_must_be_positive_minutes(tmp_path, value):
    p = tmp_path / 'config.yaml'
    p.write_text(f'refresh_minutes: {str(value).lower()}\n')
    with pytest.raises(ValueError, match='refresh_minutes'):
        load_config(p, env={})


def test_continuous_refresh_defaults_to_two_minutes():
    assert load_config(None, env={}).refresh_minutes == 2
