from pathlib import Path
from dealfinder.catalog import load_seed
from dealfinder.discovery import browser_queries

def test_console_searches_include_halo_ps2_and_all_catalog_families():
    catalog=load_seed(Path('catalog'))
    queries=browser_queries(catalog)
    assert any('halo 3' in q.lower() for q in queries[:8])
    assert any('playstation 2' in q.lower() for q in queries[:8])
    assert all(p.discovery_query in queries for p in catalog.products if p.enabled and p.discovery_query)
    assert len(queries)==len(set(queries))


def test_console_priority_keeps_existing_hardware_and_game_lot_searches():
    catalog=load_seed(Path('catalog'))
    queries=browser_queries(catalog)
    assert all(p.ebay_lot_query in queries for p in catalog.enabled_platforms())
    assert all(f'{b.ebay_query} lot' in queries for b in catalog.enabled_bulk() if b.source_product_id is None)
