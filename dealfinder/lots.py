from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TypeVar

from dealfinder.models import BulkProduct, Platform
from dealfinder.text import pick_most_specific

GAME_WORDS = r"\b(games?|carts?|cartridges?|discs?)\b"
HARDWARE_WORDS = (
    r"\b(consoles?|systems?|controllers?|dualshock|dualsense|joy ?cons?|gamepads?|handhelds?"
    r"|slim|fat|scph)\b"
)

# "with controllers", "and 1 console", "+ cables": extras that must not decide the
# bulk product (spec 6.5.2). Bulk patterns are "platform.*hardware word", so an
# extra's hardware word later in the title would otherwise win as more specific.
_ACCESSORY_CLAUSE = re.compile(
    r"(?:\b(?:with|w|and|plus|includes?|including)\b|\+) ?(?:\d+ )?(?:\S+ ){0,2}?"
    r"(?:controllers?|consoles?|cables?|games?|accessories|adapters?|power supplies)\b"
)

# Spec section 6.5. "For parts", "broken", "untested" and "as is" are allowed:
# hardware lots are bought to repair.
HARDWARE_LOT_EXCLUDES: list[str] = [
    r"\bshells? only\b",
    r"\bcases? only\b",
    r"\bhousings? only\b",
    r"\bbox(es)? only\b",
    r"\bempty box(es)?\b",
    r"\bfaceplates? only\b",
    r"\bgames? only\b",
]

# Spec section 6.6. Game lots must be working.
GAME_LOT_EXCLUDES: list[str] = [
    r"\bfor parts\b",
    r"\bnot working\b",
    r"\bbroken\b",
    r"\buntested\b",
    r"\bdamaged\b",
    r"\bcracked\b",
    r"\bscratched\b",
    r"\bresurface(d)?\b",
    r"\bas is\b",
    r"\bcases? only\b",
    r"\bmanuals? only\b",
    r"\binserts? only\b",
    r"\bempty cases?\b",
    r"\bbox only\b",
    r"\brepros?\b",
    r"\breproductions?\b",
]

T = TypeVar("T", BulkProduct, Platform)


def _match_lot(
    title_norm: str, items: Sequence[T], excludes: Sequence[str],
    quantity: int | None, min_qty: int,
) -> tuple[T, int | None] | None:
    if any(re.search(p, title_norm) for p in excludes):
        return None
    # Specificity is decided over all items, so a disabled specific entry
    # (xbox-360) rejects the title instead of handing it to a looser one (xbox).
    matches = [(i, m) for i in items if (m := re.search(i.match, title_norm))]
    item = pick_most_specific(matches)
    if item is None or not item.enabled:
        return None
    if quantity is not None and quantity < min_qty:
        return None
    return item, quantity


def match_hardware_lot(
    title_norm: str, bulk: Sequence[BulkProduct], quantity: int | None, min_qty: int,
    *, platforms: Sequence[Platform] = (),
) -> tuple[BulkProduct, int | None] | None:
    """`platforms`, when given, also marks a title naming two unrelated platforms as
    mixed: console patterns end in ".*hardware word", so in "ps2 and xbox 360 consoles"
    the ps2 match span swallows the xbox one and would otherwise win as more specific."""
    named = [(p, m) for p in platforms if (m := re.search(p.match, title_norm))]
    if len(named) >= 2 and pick_most_specific(named) is None:
        return None
    if any(re.search(p, title_norm) for p in HARDWARE_LOT_EXCLUDES):
        return None
    return _match_lot(_ACCESSORY_CLAUSE.sub(" ", title_norm), bulk, (), quantity, min_qty)


def match_game_lot(
    title_norm: str, platforms: Sequence[Platform], quantity: int | None, min_qty: int
) -> tuple[Platform, int | None] | None:
    return _match_lot(title_norm, platforms, GAME_LOT_EXCLUDES, quantity, min_qty)
