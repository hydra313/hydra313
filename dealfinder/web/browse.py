"""Shared listing search and numeric sorting; unknown values always sort last."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

from dealfinder.console_editions import PLATFORMS
from dealfinder.text import normalize

SORTS = {'recommended':'Recommended', 'condition':'Condition', 'price':'Current price',
         'profit':'Estimated profit', 'margin':'Profit margin', 'sold':'Average sold price',
         'rating':'Deal rating', 'time':'Time left'}
BRANDS = {
    'Sony':r'sony|playstation|ps[1-5]|psp|vita|dualsense|dualshock',
    'Microsoft':r'microsoft|xbox',
    'Nintendo':r'nintendo|switch|game ?boy|gamecube|wii|[23]?ds|dsi|nes|snes|n64|famicom',
    'Sega':r'sega|dreamcast|saturn|genesis|game gear|mega drive',
    'NVIDIA':r'nvidia|geforce|rtx', 'AMD':r'amd|ryzen|radeon', 'Intel':r'intel',
    'ASUS':r'asus', 'MSI':r'msi', 'Gigabyte':r'gigabyte|aorus', 'EVGA':r'evga',
    'Zotac':r'zotac', 'PNY':r'pny', 'Sapphire':r'sapphire', 'PowerColor':r'powercolor',
    'XFX':r'xfx', 'ASRock':r'asrock', 'Palit':r'palit', 'Gainward':r'gainward',
    'Dell':r'dell|alienware', 'HP':r'hp|hewlett packard', 'Lenovo':r'lenovo',
    'Atari':r'atari', 'SNK':r'snk|neo geo', 'NEC':r'nec|pc engine|turbografx',
    'Bandai':r'bandai|wonderswan', 'Panasonic':r'panasonic',
}


@dataclass
class BrowseFilters:
    q: str = ''
    brand: str = ''
    console: str = ''
    condition: str = ''
    rating: str = ''
    min_price: str = ''
    max_price: str = ''
    sort: str = 'recommended'
    direction: str = 'desc'

    def values(self) -> dict:
        values = {k:str(v).strip() for k,v in asdict(self).items()}
        values['q'] = values['q'][:200]
        if values['sort'] not in SORTS:
            values['sort'] = 'recommended'
        if values['direction'] not in ('asc','desc'):
            values['direction'] = 'desc'
        return values


def metadata(product: str, title: str) -> dict:
    # Prefer the catalog identity for console family; a title can mention bundled games.
    console = ''
    for text in (normalize(product), normalize(title)):
        matches = [(name, re.search(pattern,text)) for name,pattern in PLATFORMS.items()]
        matches = [(name,m) for name,m in matches if m]
        if matches:
            console = max(matches, key=lambda p:len(p[1].group()))[0]
            break
    text = normalize(product+' '+title)
    if not console and re.search(r'\bdualsense\b',text):
        console = 'Sony PlayStation 5'
    if not console and re.search(r'\bdualshock 4\b',text):
        console = 'Sony PlayStation 4'
    console = console.replace('Sony ','').replace('Microsoft ','').replace(' Xl',' XL')
    brands = sorted(name for name,pattern in BRANDS.items()
                    if re.search(r'\b(?:'+pattern+r')\b',text))
    return {'brands':brands, 'console':console}


def available_options(options: dict) -> dict:
    """Options stay usable when HTMX changes category without reloading the form."""
    return {**options, 'brands':sorted(set(BRANDS) | set(options['brands'])),
            'consoles':sorted({metadata(name,'')['console'] for name in PLATFORMS} | set(options['consoles']))}


def _number(text):
    try:
        number = float(text)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError,ValueError):
        return None


def browse(items: list[dict], filters: BrowseFilters) -> tuple[list[dict], dict]:
    f = filters.values()
    for d in items:
        d.update(metadata(d['product'],d['title']))
        d['margin'] = (100*d['profit']/d['total_cost']
                       if d['profit'] is not None and d['total_cost'] and d['total_cost'] > 0 else None)
    options = {'brands':sorted({b for d in items for b in d['brands']}),
               'consoles':sorted({d['console'] for d in items if d['console']}), 'sorts':SORTS}
    # Keep bookmarked filters representable even while a source is temporarily empty.
    for key in ('brand','console'):
        if f[key] and f[key].casefold() not in [v.casefold() for v in options[key+'s']]:
            options[key+'s'].append(f[key])
    minimum, maximum = _number(f['min_price']), _number(f['max_price'])
    query_console = metadata('',f['q'])['console']
    query_text = normalize(f['q'])
    if query_console:
        for pattern in PLATFORMS.values():
            query_text = re.sub(pattern,' ',query_text)
    words = query_text.split()
    def same_family(actual):
        return not query_console or actual == query_console or any(
            actual == query_console + suffix for suffix in (' Slim',' Pro',' Lite',' OLED Model',' XL'))
    def matches(d):
        text = normalize(d['title']+' '+d['product'])
        return (all(re.search(r'\b'+re.escape(word)+r'\b',text) for word in words)
                and same_family(d['console'])
                and (not f['brand'] or f['brand'].casefold() in [b.casefold() for b in d['brands']])
                and (not f['console'] or f['console'].casefold() == d['console'].casefold())
                and (not f['condition'] or f['condition'].casefold() == d.get('condition','unknown').casefold())
                and (not f['rating'] or f['rating'] == d['rating']['tone'])
                and (minimum is None or d['price'] >= minimum)
                and (maximum is None or d['price'] <= maximum))
    result = [d for d in items if matches(d)]
    def value(d):
        return {'condition':{'new':2,'used':1,'for_parts':0}.get(d.get('condition')),
                'price':d['price'], 'profit':d['profit'], 'margin':d['margin'],
                'sold':d['rating']['average'],
                'rating':{'good':3,'average':2,'overpriced':1}.get(d['rating']['tone']),
                'time':d.get('end_at')}.get(f['sort'])
    if f['sort'] != 'recommended':
        known = [d for d in result if value(d) is not None]
        unknown = [d for d in result if value(d) is None]
        result = sorted(known,key=value,reverse=f['direction']=='desc') + unknown
    return result, options


def visible_deals(items: list[dict], show_all: bool) -> list[dict]:
    """Unknown values and small/negative margins need an explicit opt-in."""
    from dealfinder.matcher import unrelated_accessory
    return [d for d in items if not unrelated_accessory(d['title'])
            and (show_all or d['rating']['tone'] == 'good')]
