from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import get_args

import yaml

from dealfinder import db
from dealfinder.config import Config
from dealfinder.console_editions import REFERENCE_FILE, load_console_editions
from dealfinder.models import BulkProduct, Category, Platform, Product

log = logging.getLogger(__name__)

_PRODUCT_FILES = ("gpus.yaml", "cpus.yaml", "consoles.yaml", "controllers.yaml")
_BULK_FILE = "bulk.yaml"
_PLATFORM_FILE = "platforms.yaml"


@dataclass
class Catalog:
    products: list[Product]
    bulk: list[BulkProduct]
    platforms: list[Platform]

    def enabled_products(self) -> list[Product]:
        return [p for p in self.products if p.enabled]

    def enabled_bulk(self) -> list[BulkProduct]:
        return [b for b in self.bulk if b.enabled]

    def enabled_platforms(self) -> list[Platform]:
        return [p for p in self.platforms if p.enabled]

    def product(self, product_id: str) -> Product:
        return _find(self.products, product_id, "product")

    def bulk_product(self, bulk_id: str) -> BulkProduct:
        return _find(self.bulk, bulk_id, "bulk product")

    def platform(self, platform_id: str) -> Platform:
        return _find(self.platforms, platform_id, "platform")

    def mask_patterns(self) -> list[str]:
        """Platform names (enabled or not): masked out of titles before reading a quantity."""
        return [p.match for p in self.platforms]


def _find(items: list, item_id: str, what: str):
    for item in items:
        if item.id == item_id:
            return item
    raise KeyError(f"unknown {what} {item_id!r}")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: top level must be a list")
    return data


def _check_regex(file: str, item_id: str, pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{file}: {item_id}: invalid regex {pattern!r}: {e}") from e


def _field(entry: dict, key: str, file: str):
    if not isinstance(entry, dict):
        raise ValueError(f"{file}: entries must be mappings, got {entry!r}")
    if entry.get(key) is None:
        raise ValueError(f"{file}: {entry.get('id', '?')}: missing {key}")
    return entry[key]


def _load_products(path: Path, seen: set[str]) -> list[Product]:
    out = []
    for e in _read(path):
        item_id = _field(e, "id", path.name)
        if item_id in seen:
            raise ValueError(f"{path.name}: duplicate id {item_id!r}")
        seen.add(item_id)
        category = _field(e, "category", path.name)
        if category not in get_args(Category):
            raise ValueError(f"{path.name}: {item_id}: unknown category {category!r}")
        exclude = tuple(e.get("exclude") or ())
        match = _field(e, "match", path.name)
        for pattern in (match, *exclude):
            _check_regex(path.name, item_id, pattern)
        out.append(Product(
            id=item_id, category=category, name=_field(e, "name", path.name),
            ebay_query=_field(e, "ebay_query", path.name), match=match, exclude=exclude,
            max_price=e.get("max_price"), source_url=e.get('source_url')))
    return out


def _load_bulk(path: Path) -> list[BulkProduct]:
    out, seen = [], set()
    for e in _read(path):
        item_id = _field(e, "id", path.name)
        if item_id in seen:
            raise ValueError(f"{path.name}: duplicate id {item_id!r}")
        seen.add(item_id)
        category = _field(e, "category", path.name)
        if category not in get_args(Category):
            raise ValueError(f"{path.name}: {item_id}: unknown category {category!r}")
        match = _field(e, "match", path.name)
        _check_regex(path.name, item_id, match)
        # repair_cost / success_rate stay None here; load_catalog fills category defaults.
        out.append(BulkProduct(
            id=item_id, category=category, name=_field(e, "name", path.name),
            ebay_query=_field(e, "ebay_query", path.name), match=match,
            repair_cost=e.get("repair_cost"), success_rate=e.get("success_rate")))
    return out


def _load_platforms(path: Path) -> list[Platform]:
    out, seen = [], set()
    for e in _read(path):
        item_id = _field(e, "id", path.name)
        if item_id in seen:
            raise ValueError(f"{path.name}: duplicate id {item_id!r}")
        seen.add(item_id)
        match = _field(e, "match", path.name)
        _check_regex(path.name, item_id, match)
        out.append(Platform(
            id=item_id, name=_field(e, "name", path.name), match=match,
            ebay_lot_query=_field(e, "ebay_lot_query", path.name)))
    return out


def load_seed(catalog_dir: Path) -> Catalog:
    """Read the six seed files; a missing file counts as empty."""
    catalog_dir = Path(catalog_dir)
    seen: set[str] = set()
    products: list[Product] = []
    for name in _PRODUCT_FILES:
        products += _load_products(catalog_dir / name, seen)
    for product in load_console_editions(catalog_dir / REFERENCE_FILE, products):
        if product.id in seen:
            raise ValueError(f'{REFERENCE_FILE}: duplicate id {product.id!r}')
        seen.add(product.id)
        products.append(product)
    return Catalog(
        products=products,
        bulk=_load_bulk(catalog_dir / _BULK_FILE),
        platforms=_load_platforms(catalog_dir / _PLATFORM_FILE),
    )


def load_catalog(catalog_dir: Path, conn: sqlite3.Connection, cfg: Config) -> Catalog:
    """Seed + custom entries, then user overrides, then category repair defaults."""
    seed = load_seed(catalog_dir)
    products = list(seed.products)
    bulk = list(seed.bulk)

    product_ids = {p.id for p in products}
    for d in db.list_custom(conn, "product"):
        if d["id"] in product_ids:
            log.warning("skipping custom product %r: id already used by a catalog entry", d["id"])
            continue
        product_ids.add(d["id"])
        products.append(Product(
            id=d["id"], category=d["category"], name=d["name"], ebay_query=d["ebay_query"],
            match=d["match"], exclude=tuple(d["exclude"]), max_price=d["max_price"],
            enabled=d["enabled"]))

    po = db.get_overrides(conn, "product_overrides")
    bo = db.get_overrides(conn, "bulk_overrides")
    plo = db.get_overrides(conn, "platform_overrides")

    def apply_product(p: Product) -> Product:
        o = po.get(p.id)
        if o is None:
            return p
        return replace(
            p,
            enabled=p.enabled if o["enabled"] is None else bool(o["enabled"]),
            max_price=p.max_price if o["max_price"] is None else o["max_price"])

    def apply_bulk(b: BulkProduct) -> BulkProduct:
        default_cost, default_rate = cfg.repair_defaults[b.category]
        cost, rate, enabled = b.repair_cost, b.success_rate, b.enabled
        o = bo.get(b.id)
        if o is not None:
            if o["enabled"] is not None:
                enabled = bool(o["enabled"])
            if o["repair_cost"] is not None:
                cost = o["repair_cost"]
            if o["success_rate"] is not None:
                rate = o["success_rate"]
        return replace(
            b, enabled=enabled,
            repair_cost=default_cost if cost is None else cost,
            success_rate=default_rate if rate is None else rate)

    def apply_platform(p: Platform) -> Platform:
        o = plo.get(p.id)
        if o is None or o["enabled"] is None:
            return p
        return replace(p, enabled=bool(o["enabled"]))

    products = [apply_product(p) for p in products]

    seed_bulk_ids = {b.id for b in bulk}
    auto_ids = {f"bulk-{p.id}" for p in products if p.category in ("gpu", "cpu")}
    bulk_ids = set(seed_bulk_ids)
    for d in db.list_custom(conn, "bulk"):
        if d["id"] in bulk_ids or d["id"] in auto_ids:
            log.warning("skipping custom bulk product %r: id already used by a catalog entry",
                        d["id"])
            continue
        bulk_ids.add(d["id"])
        bulk.append(BulkProduct(**d))
    for p in products:
        if p.category in ("gpu", "cpu"):
            bulk_id = f"bulk-{p.id}"
            if bulk_id in seed_bulk_ids:
                log.warning("skipping generated bulk product %r: id already used by a seed entry",
                            bulk_id)
                continue
            bulk.append(BulkProduct(
                id=bulk_id, category=p.category, name=p.name, ebay_query=p.ebay_query,
                match=p.match, repair_cost=None, success_rate=None, enabled=p.enabled,
                source_product_id=p.id))

    return Catalog(
        products=products,
        bulk=[apply_bulk(b) for b in bulk],
        platforms=[replace(p, enabled=False) for p in seed.platforms],  # names still mask console quantities
    )
