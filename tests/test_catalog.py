import re
import shutil
from pathlib import Path

import pytest

from dealfinder import db
from dealfinder.catalog import Catalog, load_catalog, load_seed
from dealfinder.config import Config

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def catalog_dir(tmp_path):
    d = tmp_path / "catalog"
    shutil.copytree(FIXTURES, d)
    return d


def test_load_seed_reads_all_files():
    cat = load_seed(FIXTURES)
    assert [p.id for p in cat.products] == [
        "rtx-4070-ti-super", "rx-7900-xtx", "ryzen-7-7800x3d",
        "ps5-30th-anniversary", "switch-oled-zelda-totk", "dualsense-god-of-war-ragnarok",
    ]
    assert [b.id for b in cat.bulk] == ["ps2-console", "xbox-360-controller"]
    assert [p.id for p in cat.platforms] == ["ps2", "snes"]
    xtx = cat.product("rx-7900-xtx")
    assert xtx.category == "gpu"
    assert xtx.exclude == ("water\\s*block",)
    assert xtx.max_price == 700
    assert cat.product("rtx-4070-ti-super").max_price is None
    ps2 = cat.bulk_product("ps2-console")
    assert ps2.repair_cost is None and ps2.success_rate is None
    assert cat.platform("snes").ebay_lot_query == "snes game lot"


def test_missing_seed_file_is_empty(catalog_dir):
    (catalog_dir / "cpus.yaml").unlink()
    assert [p.id for p in load_seed(catalog_dir).products if p.category == "cpu"] == []


def test_bad_regex_names_file_and_id(catalog_dir):
    (catalog_dir / "gpus.yaml").write_text(
        "- id: broken-gpu\n  category: gpu\n  name: x\n  ebay_query: x\n  match: '(unclosed'\n")
    with pytest.raises(ValueError) as exc:
        load_seed(catalog_dir)
    assert "gpus.yaml" in str(exc.value) and "broken-gpu" in str(exc.value)


def test_duplicate_id_rejected(catalog_dir):
    (catalog_dir / "platforms.yaml").write_text(
        "- {id: ps2, name: A, match: 'a', ebay_lot_query: a}\n"
        "- {id: ps2, name: B, match: 'b', ebay_lot_query: b}\n")
    with pytest.raises(ValueError) as exc:
        load_seed(catalog_dir)
    assert "platforms.yaml" in str(exc.value) and "ps2" in str(exc.value)


def test_duplicate_id_across_product_files_rejected(catalog_dir):
    (catalog_dir / "cpus.yaml").write_text(
        "- id: rx-7900-xtx\n  category: cpu\n  name: x\n  ebay_query: x\n  match: 'x'\n")
    with pytest.raises(ValueError, match="rx-7900-xtx"):
        load_seed(catalog_dir)


def test_overrides_applied(catalog_dir, conn):
    db.set_override(conn, "product_overrides", "rtx-4070-ti-super", enabled=False)
    db.set_override(conn, "product_overrides", "rx-7900-xtx", max_price=650.0)
    db.set_override(conn, "bulk_overrides", "ps2-console", repair_cost=12.0)
    db.set_override(conn, "platform_overrides", "snes", enabled=False)
    cat = load_catalog(catalog_dir, conn, Config())
    assert cat.product("rtx-4070-ti-super").enabled is False
    assert "rtx-4070-ti-super" not in [p.id for p in cat.enabled_products()]
    assert cat.product("rx-7900-xtx").max_price == 650.0
    assert cat.product("rx-7900-xtx").enabled is True
    ps2 = cat.bulk_product("ps2-console")
    assert ps2.repair_cost == 12.0
    assert ps2.success_rate == 0.60  # untouched field falls back to category default
    assert [p.id for p in cat.enabled_platforms()] == ["ps2"]
    assert cat.platform("snes").enabled is False


def test_set_override_merges_fields(conn):
    db.set_override(conn, "bulk_overrides", "b", repair_cost=5.0)
    db.set_override(conn, "bulk_overrides", "b", success_rate=0.5)
    row = db.get_overrides(conn, "bulk_overrides")["b"]
    assert (row["enabled"], row["repair_cost"], row["success_rate"]) == (None, 5.0, 0.5)
    db.set_override(conn, "bulk_overrides", "b", repair_cost=None)
    assert db.get_overrides(conn, "bulk_overrides")["b"]["repair_cost"] is None


def test_set_override_rejects_unknown_field_or_table(conn):
    with pytest.raises(ValueError):
        db.set_override(conn, "platform_overrides", "ps2", max_price=1.0)
    with pytest.raises(ValueError):
        db.set_override(conn, "listings", "x", enabled=True)


def test_null_repair_uses_category_default(catalog_dir, conn):
    cfg = Config()
    cfg.repair_defaults["console"] = (30, 0.5)
    cat = load_catalog(catalog_dir, conn, cfg)
    ps2 = cat.bulk_product("ps2-console")
    assert (ps2.repair_cost, ps2.success_rate) == (30, 0.5)
    ctl = cat.bulk_product("xbox-360-controller")
    assert (ctl.repair_cost, ctl.success_rate) == (5, 0.8)


def test_gpu_cpu_products_become_bulk_products(catalog_dir, conn):
    cat = load_catalog(catalog_dir, conn, Config())
    b = cat.bulk_product("bulk-rtx-4070-ti-super")
    p = cat.product("rtx-4070-ti-super")
    assert b.source_product_id == p.id
    assert (b.match, b.ebay_query, b.category) == (p.match, p.ebay_query, "gpu")
    assert (b.repair_cost, b.success_rate) == (40, 0.40)
    cpu = cat.bulk_product("bulk-ryzen-7-7800x3d")
    assert (cpu.repair_cost, cpu.success_rate) == (10, 0.30)
    ids = [x.id for x in cat.bulk]
    assert "bulk-ps5-30th-anniversary" not in ids
    assert "bulk-dualsense-god-of-war-ragnarok" not in ids
    assert ids[:2] == ["ps2-console", "xbox-360-controller"]


def test_custom_product_included(catalog_dir, conn):
    db.add_custom(conn, "product", {
        "id": "rtx-5090", "category": "gpu", "name": "RTX 5090", "ebay_query": "rtx 5090",
        "match": r"\b5090\b", "exclude": ["box only"], "max_price": 1800.0})
    db.add_custom(conn, "bulk", {
        "id": "n64-console", "category": "console", "name": "N64 console",
        "ebay_query": "n64 console", "match": r"\bn64\b.*\bconsole\b",
        "repair_cost": 15.0, "success_rate": 0.7})
    cat = load_catalog(catalog_dir, conn, Config())
    p = cat.product("rtx-5090")
    assert p.exclude == ("box only",) and p.max_price == 1800.0 and p.enabled
    assert cat.bulk_product("bulk-rtx-5090").source_product_id == "rtx-5090"
    n64 = cat.bulk_product("n64-console")
    assert (n64.repair_cost, n64.success_rate) == (15.0, 0.7)
    listed = db.list_custom(conn, "product")
    assert listed[0]["id"] == "rtx-5090" and listed[0]["exclude"] == ["box only"]
    assert db.list_custom(conn, "bulk")[0]["id"] == "n64-console"


def test_custom_overrides_apply_to_custom_entries(catalog_dir, conn):
    db.add_custom(conn, "product", {
        "id": "rtx-5090", "category": "gpu", "name": "RTX 5090", "ebay_query": "q",
        "match": r"\b5090\b"})
    db.set_override(conn, "product_overrides", "rtx-5090", enabled=False)
    assert load_catalog(catalog_dir, conn, Config()).product("rtx-5090").enabled is False


def test_add_custom_validates(catalog_dir, conn):
    base = {"id": "x1", "category": "gpu", "name": "X", "ebay_query": "x", "match": "x"}
    with pytest.raises(ValueError, match="x1"):
        db.add_custom(conn, "product", {**base, "match": "(unclosed"})
    with pytest.raises(ValueError):
        db.add_custom(conn, "product", {**base, "exclude": ["[bad"]})
    with pytest.raises(ValueError):
        db.add_custom(conn, "product", {**base, "category": "toaster"})
    assert db.list_custom(conn, "product") == []
    db.add_custom(conn, "product", base)
    with pytest.raises(ValueError, match="x1"):
        db.add_custom(conn, "product", base)
    with pytest.raises(ValueError, match="rx-7900-xtx"):
        db.add_custom(conn, "product", {**base, "id": "rx-7900-xtx"},
                      reserved_ids={"rx-7900-xtx"})
    assert len(db.list_custom(conn, "product")) == 1


def test_custom_id_clashing_with_seed_rejected_on_load(catalog_dir, conn):
    db.add_custom(conn, "product", {
        "id": "rx-7900-xtx", "category": "gpu", "name": "X", "ebay_query": "x", "match": "x"})
    with pytest.raises(ValueError, match="rx-7900-xtx"):
        load_catalog(catalog_dir, conn, Config())


def test_mask_patterns_contains_platforms(catalog_dir, conn):
    cat = load_catalog(catalog_dir, conn, Config())
    pats = cat.mask_patterns()
    assert pats == [p.match for p in cat.platforms]
    assert r"\b(ps2|playstation\s*2)\b" in pats
    # bulk patterns are not mask patterns
    assert not any(b.match in pats for b in cat.bulk)
    for p in pats:
        re.compile(p)


def test_mask_patterns_include_disabled_platforms(catalog_dir, conn):
    db.set_override(conn, "platform_overrides", "snes", enabled=False)
    cat = load_catalog(catalog_dir, conn, Config())
    assert len(cat.mask_patterns()) == 2


def test_catalog_lookup_unknown_id_raises():
    cat = Catalog([], [], [])
    with pytest.raises(KeyError):
        cat.product("nope")
