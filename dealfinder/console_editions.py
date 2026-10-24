"""Conservative matching for the sourced console-edition reference snapshot.

Reference data is never fetched at runtime. Regional packaging records with the
same hardware signature share one product; ordinary software bundles, prototypes,
accessories and unidentifiable records remain in the audit, outside the watchlist.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from dealfinder.models import Product
from dealfinder.text import normalize

REFERENCE_FILE = 'console_editions.json'
REFERENCE_BASE = 'https://consolevariations.com/collectibles/'
_SOURCE_REVIEW = {
    'authentication': r'under.{0,50}investigat|authenticity.{0,40}(unknown|unconfirmed|question)|authentication.{0,40}(unknown|unconfirmed|question)',
    'unreleased': r'\bunreleased\b|never (officially )?released|not (officially )?released|\bcancel[le]*d\b|\bprototype\b',
    'unofficial': r'\bfan[ -]?made\b|\baftermarket\b|\bunofficial\b|\bhome[ -]?made\b|\breshell(?:ed)?\b',
}


def reference_review_flags(description: str) -> list[str]:
    """Extract review flags without storing the site's descriptive text."""
    return [name for name, pattern in _SOURCE_REVIEW.items()
            if re.search(pattern, description or '', re.I)]


_REGION = re.compile(r'\[[^\]]+\]')
_STOP = set('console consoles system systems model edition '
            'limited special bundle pack set the of and a an new pokemon center call duty '
            'promotional promo sweepstakes sweepstake contest prize'.split())
_UNSUITABLE = re.compile(r'\b(prototype|development|dev ?kit|kiosk|dummy|mock ?up|'
                         r'demo|display|replacement|replica|reproduction|repro|'
                         r'shell|housing|signed|autographed|autograph)\b')
_ACCESSORY = re.compile(r'\b(controllers?|gamepads?|joy ?cons?|dualsense|dualshock|'
                       r'covers?|plates?|faceplates?|wraps?|decals?|stickers?|skins?|'
                       r'shells?|housings?|cases?|pouches?|chargers?|cables?|adapters?|'
                       r'motherboards?|parts|manuals?|inserts?|stands?|docks?|stylus|'
                       r'headsets?|screens?|sensors?|power supplies|batteries|'
                       r'hard drives?|hdds?|boxes|box|games?|cartridges?|discs?)\b')
_NON_FACTORY = re.compile(r'\b(custom|reshell(?:ed)?|re ?shell(?:ed)?|aftermarket|'
                         r'replica|reproduction|repro|modded|modified|modification)\b')
_PRIMARY_END = re.compile(r'\b(with|plus|includes|including)\b|\+')
_HARDWARE_BUNDLES = {
    'Microsoft Xbox 360': r'gears (?:of )?war 3|halo [34]|halo reach|r2 d2|modern warfare [23]|resident evil 5',
    'Microsoft Xbox One': r'advanced warfare|forza motorsports? 6|halo 5',
    'Microsoft Xbox One S': r'gears (?:of )?war 4|minecraft|fortnite',
    'Microsoft Xbox One X': r'gears (?:of )?war 5|gears 5|cyberpunk|scorpio',
    'Microsoft Xbox Series X': r'halo infinite|galaxy black|25th anniversary',
    'Sony PlayStation 4': r'metal gear solid|batman|darth vader|taken king',
    'Sony PlayStation 4 Slim': r'luna|days of play',
    'Sony PlayStation 4 Pro': r'god of war|spider man|kingdom hearts|500 million|death stranding|last of us|monster hunter|battlefront 2',
    'PlayStation 5 Slim': r'30th anniversary|ghost of yotei',
    'Nintendo Switch': r'animal crossing|smash bros|lets? go|let s go|diablo|dragon quest|tsum tsum|mario odyssey|mario red|monster hunter rise',
    'Nintendo Switch OLED Model': r'splatoon 3|scarlet|violet|tears of the kingdom|mario red',
    'Nintendo Switch Lite': r'hyrule|dialga|palkia|zacian|zamazenta',
    'Nintendo Wii U': r'wind waker',
    'Gamecube': r'gundam char|hanshin',
    'Sony PSP X000 Series': r'crisis core|dissidia|birth by sleep|monster hunter',
}


# Hardware boundaries matter: an original model cannot consume its successor or
# a Slim/Pro/New/Lite/OLED variant. Match only the platform here; edition words
# are checked separately so thousands of records need only dozens of regexes.
PLATFORMS = {
    'Microsoft Xbox': r'\bxbox\b(?! ?(?:360|one|series)\b)',
    'Microsoft Xbox 360': r'\bxbox ?360\b',
    'Microsoft Xbox One': r'\bxbox ?one\b(?! ?[sx]\b)',
    'Microsoft Xbox One S': r'\bxbox ?one s\b',
    'Microsoft Xbox One X': r'\bxbox ?one x\b',
    'Microsoft Xbox Series S': r'\bxbox series s\b',
    'Microsoft Xbox Series X': r'\bxbox series x\b',
    'Nintendo Switch': r'\b(?:nintendo )?switch\b(?! ?(?:2|lite|oled)\b)',
    'Nintendo Switch Lite': r'\b(?:nintendo )?switch lite\b',
    'Nintendo Switch OLED Model': r'\b(?:nintendo )?switch oled(?: model)?\b',
    'Nintendo Switch 2': r'\b(?:nintendo )?switch 2\b',
    'Nintendo 3DS': r'(?<!new )(?<!new nintendo )\b(?:nintendo )?3ds\b(?! ?(?:xl|ll)\b)',
    'Nintendo 3DS Xl': r'(?<!new )(?<!new nintendo )\b(?:nintendo )?3ds ?(?:xl|ll)\b',
    'New Nintendo 3DS': r'\bnew (?:nintendo )?3ds\b(?! ?(?:xl|ll)\b)',
    'New Nintendo 3DS Xl': r'\bnew (?:nintendo )?3ds ?(?:xl|ll)\b',
    'Nintendo 2DS': r'\b(?:nintendo )?2ds\b(?! ?(?:xl|ll)\b)',
    'New Nintendo 2DS XL': r'\bnew (?:nintendo )?2ds ?(?:xl|ll)\b',
    'Nintendo DS': r'\b(?:nintendo )?ds\b(?! ?(?:lite|i|xl|ll)\b)',
    'Nintendo DS Lite': r'\b(?:nintendo )?ds ?lite\b',
    'Nintendo DSi': r'\b(?:nintendo )?dsi\b(?! ?(?:xl|ll)\b)',
    'Nintendo DSi XL': r'\b(?:nintendo )?dsi ?(?:xl|ll)\b',
    'Game Boy': r'\b(?:game ?boy|gb)\b(?! ?(?:advance|color|colour|light|pocket|micro)\b)',
    'Game Boy Color': r'\b(?:game ?boy colou?r|gbc)\b',
    'Game Boy Pocket': r'\b(?:game ?boy pocket|gbp)\b',
    'Game Boy Light': r'\bgame ?boy light\b',
    'Game Boy Micro': r'\b(?:game ?boy micro|gbm)\b',
    'Game Boy Advance': r'\b(?:game ?boy advance|gba)\b(?! ?sp\b)',
    'Game Boy Advance SP': r'\b(?:game ?boy advance|gba) sp\b',
    'Nintendo Wii': r'\b(?:nintendo )?wii\b(?! ?u\b)',
    'Nintendo Wii U': r'\b(?:nintendo )?wii ?u\b',
    'Gamecube': r'\b(?:nintendo )?game ?cube\b',
    'Nintendo 64': r'\b(?:nintendo ?64|n64)\b(?! ?dd\b)',
    'Nintendo 64 DD': r'\b(?:nintendo ?64 ?dd|n64 ?dd|64dd)\b',
    'SNES': r'\b(?:snes|super nintendo(?: entertainment system)?|super famicom)\b',
    'NES': r'\b(?:nes|nintendo entertainment system)\b',
    'Famicom': r'(?<!super )\bfamicom\b(?! disk\b)',
    'Famicom Disk System': r'\bfamicom disk(?: system)?\b',
    'Sony PlayStation': r'\b(?:ps1|psone|playstation)\b(?! ?(?:[2345]|vita|tv|portable)\b)',
    'Sony PlayStation 2': r'\b(?:ps2|playstation ?2)\b(?! ?slim\b)',
    'Sony PlayStation 2 Slim': r'\b(?:ps2|playstation ?2) slim\b',
    'Sony PlayStation 3': r'\b(?:ps3|playstation ?3)\b(?! ?(?:slim|super)\b)',
    'Sony PlayStation 3 Slim': r'\b(?:ps3|playstation ?3) slim\b',
    'Sony PlayStation 3 Super Slim': r'\b(?:ps3|playstation ?3) super ?slim\b',
    'Sony PlayStation 4': r'\b(?:ps4|playstation ?4)\b(?! ?(?:slim|pro)\b)',
    'Sony PlayStation 4 Slim': r'\b(?:ps4|playstation ?4) slim\b',
    'Sony PlayStation 4 Pro': r'\b(?:ps4|playstation ?4) pro\b',
    'Sony PlayStation 5': r'\b(?:ps5|playstation ?5)\b(?! ?(?:slim|pro)\b)',
    'PlayStation 5 Slim': r'\b(?:ps5|playstation ?5) slim\b',
    'PlayStation 5 Pro': r'\b(?:ps5|playstation ?5) pro\b',
    'Sony PlayStation Vita': r'\b(?:ps ?vita|playstation vita)\b(?! ?tv\b)',
    'Sony PlayStation  TV': r'\b(?:ps ?tv|playstation tv|ps ?vita tv)\b',
    'Sony PSP X000 Series': r'\b(?:psp|playstation portable)\b(?! ?go\b)',
    'Sony PSP Go': r'\bpsp ?go\b',
    'Sega Genesis Mega Drive': r'\b(?:sega )?(?:genesis|mega ?drive)\b',
    'Sega Dreamcast': r'\b(?:sega )?dreamcast\b',
    'Sega Saturn': r'\b(?:sega )?saturn\b',
    'Sega Game Gear': r'\b(?:sega )?game ?gear\b',
    'Neo Geo Pocket': r'\bneo ?geo pocket\b(?! ?colou?r\b)',
    'Neo Geo Pocket Color': r'\bneo ?geo pocket colou?r\b',
    'WonderSwan': r'\bwonder ?swan\b(?! ?colou?r\b)',
    'WonderSwan Color': r'\bwonder ?swan colou?r\b',
    'Game & Watch': r'\bgame and watch\b(?! ?mini classics?\b)',
    'Game & Watch Mini Classics': r'\b(?:game and watch )?mini classics?\b',
    'TurboGrafx-16': r'\b(?:turbografx ?16|pc engine)\b',
    'Panasonic 3DO': r'\bpanasonic(?: real)? 3do\b',
    'Sega Mega CD': r"\b(?:sega )?(?:mega ?cd|cdx|multi ?mega|wondermega)\b|\bgenesis x eye\b",
    'Sega Master System': r'\b(?:sega )?master system\b',
}


def edition_text(text: str) -> str:
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    text = normalize(text).replace('gray', 'grey')
    text = re.sub(r'\bspiderman\b', 'spider man', text)
    text = re.sub(r'\bsnes\b', 'super nintendo', text)
    text = re.sub(r'\blove ?plus\s*\+?', 'loveplus', text)
    text = re.sub(r'\bmw ?([23])\b', r'modern warfare \1', text)
    roman = {'ii':'2', 'iii':'3', 'iv':'4', 'v':'5', 'vi':'6', 'vii':'7', 'viii':'8',
             'ix':'9', 'xiii':'13', 'xv':'15', 'xvi':'16'}
    return re.sub(r'\b(?:' + '|'.join(roman) + r')\b', lambda m: roman[m[0]], text)


def primary_title(text: str) -> str:
    return _PRIMARY_END.split(text, maxsplit=1)[0]


def _identity(pattern: str, text: str) -> str:
    remainder = re.sub(pattern, ' ', text, count=1).strip()
    return re.sub(r'^(?:(?:sony|microsoft|nintendo|sega)\s+)+', '', remainder)


def reference_spec(row: dict) -> tuple[dict | None, str]:
    """Return a matchable hardware signature, or an auditable exclusion reason."""
    if row.get('review_flags'):
        return None, 'Source authenticity, unofficial or unreleased status requires review'
    if row.get('release') not in ('official', 'licensed'):
        return None, 'Not marked official or licensed'
    if row.get('limited') not in ('yes', 'exclusive', 'sweepstake', 'prize'):
        return None, 'Not marked as a special release'
    name = ' '.join(_REGION.sub('', row['name']).split())
    text = edition_text(name)
    if _UNSUITABLE.search(text) or _NON_FACTORY.search(text):
        return None, 'Prototype, custom, signed, accessory or display record'
    if re.search(r'\bconsoles\b', text):
        return None, 'Multiple-design source record; individual hardware requires review'
    if ((row.get('bundle') or re.search(r'\b(bundle|pack|set)\b', text))
            and row['limited'] not in ('sweepstake', 'prize')
            and not re.search(_HARDWARE_BUNDLES.get(row['platform'], r'(?!)'), text)):
        return None, 'Software/packaging bundle; distinctive hardware requires review'
    if primary_title(text) != text:
        return None, 'Secondary bundle/accessory description requires review'
    family = row['platform']
    pattern = PLATFORMS.get(family)
    if pattern is None:
        pattern = r'\b' + r' ?'.join(re.escape(w) for w in edition_text(family).split()) + r'\b'
    platform = re.search(pattern, text)
    if platform is None:
        return None, 'Platform name does not identify the hardware unambiguously'
    identifiers = _identity(pattern, text)
    hardware_words = re.sub(r'\b(game of thrones|squid game|game face|game awards)\b', '', identifiers)
    if _ACCESSORY.search(hardware_words):
        return None, 'Accessory or game rather than identifiable edition hardware'
    tokens = tuple(sorted(set(re.findall(r'\w+', identifiers)) - _STOP))
    if (not tokens or set(tokens) <= {'kinect', 'elite', 'arcade', 'pro', 'slim'}
            or not any(len(t) > 2 and not t.isdigit() for t in tokens)):
        return None, 'No distinctive edition identifier'
    # Reject accessories still classified as consoles in the external index.
    leftovers = ' '.join(t for t in re.findall(r'\w+', identifiers) if t not in tokens)
    if _ACCESSORY.search(leftovers.replace('console', '')):
        return None, 'Accessory rather than complete hardware'
    return {'name': name, 'pattern': pattern, 'tokens': tokens, 'family': family}, ''


def match_edition(product: Product, title: str, tokens: set[str]):
    if not all(token in tokens for token in product.required_tokens):
        return None
    platform = re.search(product.match, title)
    if platform is None or _NON_FACTORY.search(title):
        return None
    identity = _identity(product.match, title)
    identity_tokens = set(re.findall(r'\w+', identity))
    if not all(token in identity_tokens for token in product.required_tokens):
        return None
    handheld = re.search(r'game boy|nintendo [23]?dsi?|psp|vita|swan|pocket|'
                         r'game & watch|pokemon mini|lynx|game gear',
                         product.discovery_query or '', re.I)
    if not handheld and not re.search(r'\b(console|system|unit|handheld)\b', title):
        return None
    if product.promotional and not re.search(
            r'\b(promo(?:tional)?|sweepstakes?|contest|prize)\b|'
            r'\b(?:limited|special) edition console\b', title):
        return None
    # The item may include accessories after "with"; they cannot be the item
    # being sold. Theme words such as Game of Thrones are part of the identity.
    residual = identity
    residual = ' '.join(t for t in re.findall(r'\w+', residual)
                        if t not in product.required_tokens)
    if _ACCESSORY.search(residual):
        return None
    return re.match(r'^.*$', title)


@lru_cache(maxsize=4)
def _load(path: str, mtime: int, size: int, curated: tuple[Product, ...]):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    groups: dict[tuple, Product] = {}
    counts: Counter = Counter()
    decisions = []
    for row in data['records']:
        if row.get('review_flags'):
            counts['Source authenticity, unofficial or unreleased status requires review'] += 1
            decisions.append((row['slug'], 'Source authenticity, unofficial or unreleased status requires review'))
            continue
        linked = data.get('identity_mappings', {}).get(row['slug'])
        if linked:
            if not any(p.id == linked for p in curated):
                raise ValueError(f'Unknown curated console identity: {linked}')
            counts['Covered by existing catalog entry'] += 1
            decisions.append((row['slug'], linked))
            continue
        spec, reason = reference_spec(row)
        if spec is None:
            counts[reason] += 1
            decisions.append((row['slug'], reason))
            continue
        title = normalize(spec['name'] + ' limited edition console')
        existing = [p for p in curated if p.category == 'console'
                    and re.search(p.match, title) and not any(re.search(e, title) for e in p.exclude)]
        if len(existing) == 1:
            counts['Covered by existing catalog entry'] += 1
            decisions.append((row['slug'], existing[0].id))
            continue
        key = (spec['family'], spec['tokens'])
        if key in groups:
            if groups[key].promotional and row['limited'] in ('yes', 'exclusive'):
                groups[key] = replace(groups[key], promotional=False, name=spec['name'],
                                      source_url=REFERENCE_BASE + row['slug'])
            counts['Regional/duplicate hardware signature'] += 1
            decisions.append((row['slug'], groups[key].id))
            continue
        promotional = row['limited'] in ('sweepstake', 'prize')
        name = spec['name']
        if not re.search(r'\b(console|system|unit|handheld)\b', name, re.I):
            name += ' Console'
        if promotional:
            name += ' (Promotional edition)'
        product = Product(id='cv-' + row['slug'], category='console', name=name,
                          ebay_query=spec['name'], match=spec['pattern'],
                          required_tokens=spec['tokens'],
                          source_url=REFERENCE_BASE + row['slug'],
                          discovery_query=spec['family'] + ' console', promotional=promotional)
        groups[key] = product
        counts['Added edition'] += 1
        decisions.append((row['slug'], product.id))
    return tuple(groups.values()), dict(counts), tuple(decisions)


def load_console_editions(path: Path, curated: list[Product]) -> list[Product]:
    if not path.exists():
        return []
    stat = path.stat()
    return list(_load(str(path.resolve()), stat.st_mtime_ns, stat.st_size, tuple(curated))[0])


def audit_console_editions(path: Path, curated: list[Product]) -> tuple[dict, tuple]:
    stat = path.stat()
    _, counts, decisions = _load(str(path.resolve()), stat.st_mtime_ns, stat.st_size, tuple(curated))
    return counts, decisions
