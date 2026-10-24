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


def test_console_priority_keeps_hardware_lots_but_removes_game_lots():
    catalog=load_seed(Path('catalog'))
    queries=browser_queries(catalog)
    assert not any(p.ebay_lot_query in queries for p in catalog.enabled_platforms())
    assert all(f'{b.ebay_query} lot' in queries for b in catalog.enabled_bulk() if b.source_product_id is None)


def test_legacy_game_queries_are_filtered_from_custom_source_configuration():
    from dealfinder.discovery import broad_queries
    catalog=load_seed(Path('catalog'))
    queries=broad_queries(catalog,['ps2 game lot','video game lot','games lot','console lot'])
    assert 'console lot' in queries
    assert not any('game lot' in q or 'games lot' in q for q in queries)
