from __future__ import annotations

import re

from dealfinder.catalog import Catalog
from dealfinder.config import Config
from dealfinder.lots import GAME_WORDS, HARDWARE_WORDS, match_game_lot, match_hardware_lot
from dealfinder.matcher import match_single
from dealfinder.models import Classified, Listing
from dealfinder.text import mask, normalize, parse_quantity, quantity_mask

# "console with 10 games": parse_quantity skips counts after "with", so the
# bundle check also looks for a game count directly (read on the quantity-masked
# title, so "switch 2 console with game" or "500 gb" never count).
_GAME_COUNT = re.compile(r"\b(\d+)\b(?=(?: \S+){0,3} games\b)")
# Masked with the platform names before the game/hardware word checks:
# "disc edition" / "slim disc" / "disc console" are not game discs, "nintendo
# entertainment system" not hardware.
_NOT_WORDS = (r"\bdisc (edition|version|drive)\b", r"\bdigital edition\b", r"\bslim disc\b",
              r"\bdisc(?= (consoles?|systems?)\b)")
# These are edition/game names, not claims about the number of consoles.
# Mask only the complete phrase; an outer "collection of" or "lot of 3" remains.
_EDITION_QUANTITY_MASKS = (
    r'\b(?:tomodachi|eternal|(?:the )?master ?chief) collection\b',
    r'\bfinal fantasy (?:x|10) (?:x ?2|10 ?2)\b',
)


def _has_game_count(title_norm: str, mask_patterns: list[str]) -> bool:
    return any(
        int(m[1]) >= 2 for m in _GAME_COUNT.finditer(quantity_mask(title_norm, mask_patterns)))


def classify(listing: Listing, catalog: Catalog, cfg: Config) -> Classified | None:
    """Spec section 6.3: route a listing to exactly one path, or reject it."""
    if not listing.shipping_offered:
        return None
    title_norm = normalize(listing.title)
    mask_patterns = catalog.mask_patterns()
    quantity = parse_quantity(mask(title_norm, _EDITION_QUANTITY_MASKS), mask_patterns)
    names_platform = any(re.search(p.match, title_norm) for p in catalog.platforms)
    words = mask(title_norm, (*mask_patterns, *_NOT_WORDS))
    has_games = re.search(GAME_WORDS, words) is not None
    has_hardware = re.search(HARDWARE_WORDS, words) is not None

    # A count of 1 can never be a game lot (min_game_lot >= 2), so such titles
    # ("PS5 Spider-Man 2 Limited Edition Bundle with game") go to the single path.
    if names_platform and has_games and not has_hardware and quantity != 1:
        lot = match_game_lot(title_norm, catalog.platforms, quantity, cfg.min_game_lot)
        if lot is None:
            return None
        platform, qty = lot
        return Classified(kind="game_lot" if qty is not None else "scan_only",
                          key=platform.id, condition=None, quantity=qty, category=None)

    if names_platform and has_games and has_hardware and (
            quantity != 1 or _has_game_count(title_norm, mask_patterns)):
        # Console + games bundle: neither lot type. A lone known product ("Halo 3
        # Special Edition Console Bundle with 5 games") is still valued as that
        # product; ignoring the games only understates it.
        return _classify_single(listing, catalog) if quantity == 1 else None

    if quantity is None or quantity >= 2:
        lot = match_hardware_lot(title_norm, catalog.bulk, quantity, cfg.min_hardware_lot,
                                 platforms=catalog.platforms)
        if lot is None:
            return None
        bulk, qty = lot
        return Classified(kind="hardware_lot" if qty is not None else "scan_only",
                          key=bulk.id, condition=None, quantity=qty, category=bulk.category)

    return _classify_single(listing, catalog)


def _classify_single(listing: Listing, catalog: Catalog) -> Classified | None:
    single = match_single(listing.title, catalog.products, listing.condition_id)
    if single is None:
        return None
    product, condition = single
    return Classified(kind="single", key=product.id, condition=condition, quantity=1,
                      category=product.category)
