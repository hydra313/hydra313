"""Broad console discovery first; retain exact identity queries for valuation."""
from itertools import zip_longest
import re
from dealfinder.text import normalize

def is_game_lot_query(query):
    text=normalize(query)
    return bool(re.search(r'\bgames?\b',text) and re.search(r'\b(?:lots?|bulk|collection)\b',text))

def browser_queries(catalog):
    groups={k:[] for k in ('console','controller','gpu','cpu')}
    console_enabled=any(p.enabled and p.category=='console' for p in catalog.products)
    if console_enabled:
        groups['console'] += ['halo 3 xbox 360 console','playstation 2 console',
                              'limited edition console','special edition console',
                              'nintendo limited edition console','playstation limited edition console',
                              'xbox limited edition console']
    for p in catalog.enabled_products():
        groups[p.category].append(p.discovery_query or p.ebay_query)
    # Each console family gets a turn before long edition-specific searches.
    consoles=groups['console']
    families=list(dict.fromkeys(p.discovery_query for p in catalog.enabled_products()
                                if p.category=='console' and p.discovery_query))
    groups['console']=consoles[:7]+families+consoles[7:] if console_enabled else consoles
    groups['hardware_lot']=[f'{b.ebay_query} lot' for b in catalog.enabled_bulk() if b.source_product_id is None]
    groups={k:list(dict.fromkeys(v)) for k,v in groups.items()}
    # Console-focused discovery still rotates other hardware. Two console searches
    # per round prevent the large component catalog from monopolizing collection.
    consoles=groups.pop('console')
    rows=zip_longest(consoles[::2],consoles[1::2],*groups.values())
    return list(dict.fromkeys(q for row in rows for q in row if q))


def broad_queries(catalog, configured):
    if not configured:
        return []
    families=[p.discovery_query for p in catalog.enabled_products()
              if p.category=='console' and p.discovery_query]
    return list(dict.fromkeys([q for q in configured if not is_game_lot_query(q)]+families))
