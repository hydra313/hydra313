from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping

import yaml

from dealfinder.models import Category

_BROAD_QUERIES = [
    "halo 3 xbox 360", "playstation 2", "ps2", "limited edition console",
    "special edition console", "rtx", "geforce", "ryzen", "intel core", "ps5", "xbox series",
    "switch oled", "dualsense", "limited edition controller", "console lot",
    "controller lot", "video game lot", "games lot", "limited edition console",
    "special edition console", "halo xbox 360",
    "playstation", "xbox", "nintendo", "game boy", "psp", "vita", "dreamcast",
    "sega", "gamecube", "n64", "snes", "wii", "3ds", "2ds", "ds lite",
    "neo geo", "pc engine", "wonderswan", "atari",
]

_SECRET_ENV = {
    "ebay_client_id": "EBAY_CLIENT_ID",
    "ebay_client_secret": "EBAY_CLIENT_SECRET",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "discord_webhook_url": "DISCORD_WEBHOOK_URL",
}


@dataclass
class Config:
    # storage / scheduling
    db_path: str = "dealfinder.db"
    catalog_dir: Path = Path("catalog")
    refresh_minutes: int = 2
    ebay_full_pass_hours: int = 3

    # scoring
    sell_fee_rate: float = 0.1325
    sell_shipping: dict[str, float] = field(
        default_factory=lambda: {"gpu": 20, "cpu": 8, "console": 30, "controller": 10})
    buy_shipping_estimate: dict[str, float] = field(
        default_factory=lambda: {"gpu": 20, "cpu": 8, "console": 30, "controller": 10})
    lot_ship_base: float = 8
    lot_ship_per_game: float = 0.40
    min_profit: float = 30
    min_lot_profit: float = 50
    min_discount: float = 0.20
    min_hardware_lot: int = 3
    min_game_lot: int = 5
    rare_item_threshold: float = 50
    tax_rate: float = 0
    market_ttl_hours: int = 24
    item_price_ttl_days: int = 30
    min_comps: int = 5
    min_item_comps: int = 3
    repair_defaults: dict[Category, tuple[float, float]] = field(
        default_factory=lambda: {
            "console": (25, 0.60),
            "controller": (8, 0.70),
            "gpu": (40, 0.40),
            "cpu": (10, 0.30),
        })

    # per-source fees
    buyer_premium_pct: dict[str, float] = field(
        default_factory=lambda: {"ebay": 0.0, "shopgoodwill": 0.0, "hibid": 0.15})
    handling_fee: dict[str, float] = field(
        default_factory=lambda: {"ebay": 0.0, "shopgoodwill": 5.0, "hibid": 0.0})

    # search
    hibid_mode: str = "browser"  # browser page import; api is the legacy, unverified adapter
    hibid_snapshot_minutes: int = 30
    broad_queries: dict[str, list[str]] = field(
        default_factory=lambda: {
            "shopgoodwill": list(_BROAD_QUERIES),
            "hibid": list(_BROAD_QUERIES),
        })

    # vision scanning
    vision_model: str = "claude-opus-5-5"
    vision_prices: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "claude-opus-5-5": (4.00, 20.00),
            # server-side fallback models
            "claude-opus-5": (5.0, 25.0),
            "claude-opus-4-8": (5.0, 25.0),
        })
    scan_cost_ceiling: float = 0.50  # one scan including a fallback-model retry
    initial_daily_cap: float = 5.0
    initial_monthly_cap: float = 50.0
    initial_max_scan_price: float = 300.0

    # notifications
    notify_reasons: dict[str, bool] = field(
        default_factory=lambda: {
            "photo_find": True,
            "target_hit": True,
            "hardware_lot_deal": True,
            "game_lot_deal": True,
            "resale_deal": False,
        })

    # API secrets from the environment; Discord also supports Windows protected setup.
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    anthropic_api_key: str | None = None
    discord_webhook_url: str | None = None


_FIELD_NAMES = {f.name for f in fields(Config)}


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    """Defaults, overridden by YAML keys (dicts merge one level deep); secrets from env."""
    cfg = Config()
    if path is not None and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top level must be a mapping")
        for key, value in data.items():
            if key not in _FIELD_NAMES:
                raise ValueError(f"{path}: unknown config key {key!r}")
            if key in _SECRET_ENV:
                continue  # secrets come only from the environment
            current = getattr(cfg, key)
            if isinstance(current, dict):
                if not isinstance(value, dict):
                    raise ValueError(f"{path}: {key} must be a mapping")
                merged = {**current, **value}
                if key in ("repair_defaults", "vision_prices"):
                    merged = {k: tuple(v) for k, v in merged.items()}
                setattr(cfg, key, merged)
            elif isinstance(current, Path):
                setattr(cfg, key, Path(value))
            else:
                setattr(cfg, key, value)
    env = os.environ if env is None else env
    for attr, var in _SECRET_ENV.items():
        setattr(cfg, attr, env.get(var) or None)
    if type(cfg.refresh_minutes) is not int or not 1 <= cfg.refresh_minutes <= 1440:
        raise ValueError("refresh_minutes must be an integer between 1 and 1440")
    if cfg.hibid_mode not in ("browser", "api", "off"):
        raise ValueError("hibid_mode must be browser, api or off")
    if (type(cfg.hibid_snapshot_minutes) is not int or cfg.hibid_snapshot_minutes <= 0
            or cfg.hibid_snapshot_minutes > 120):
        raise ValueError("hibid_snapshot_minutes must be an integer between 1 and 120")
    return cfg
