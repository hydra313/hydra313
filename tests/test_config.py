from dealfinder.config import load_config


def test_defaults_without_file():
    cfg = load_config(None, env={})
    assert cfg.sell_fee_rate == 0.1325 and cfg.min_lot_profit == 50
    assert cfg.sell_shipping == {"gpu": 20, "cpu": 8, "console": 30, "controller": 10}
    assert cfg.repair_defaults["console"] == (25, 0.60)
    assert cfg.notify_reasons["resale_deal"] is False
    assert cfg.anthropic_api_key is None


def test_yaml_overrides_merge_one_level(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("min_profit: 40\nsell_shipping: {gpu: 25}\n")
    cfg = load_config(p, env={})
    assert cfg.min_profit == 40
    assert cfg.sell_shipping == {"gpu": 25, "cpu": 8, "console": 30, "controller": 10}


def test_secrets_from_env():
    cfg = load_config(None, env={"DISCORD_WEBHOOK_URL": "https://d/x", "ANTHROPIC_API_KEY": "k"})
    assert cfg.discord_webhook_url == "https://d/x" and cfg.anthropic_api_key == "k"
