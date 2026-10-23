from __future__ import annotations

import re
from collections.abc import Sequence

from dealfinder.models import Category, Condition, Product
from dealfinder.text import normalize, pick_most_specific

# Spec section 6.4. All patterns run against normalize()d (lowercase) titles,
# where "as-is" is already "as is" and "w/" is "with".
GLOBAL_EXCLUDES: list[str] = [
    r"\bfor parts\b",
    r"\bparts only\b",
    r"\bnot working\b",
    r"\bbroken\b",
    r"\buntested\b",
    r"\bas is\b",
    r"\bbox only\b",
    r"\bempty box\b",
    r"\bno gpu\b",
    r"\bno cpu\b",
    r"\bbent pins?\b",
    r"\bengineering samples?\b",
    r"\bes\b",
    r"\bqs\b",
    r"\bread description\b",
    # broken or unverified single items
    r"\bnon working\b",
    r"\bnot tested\b",
    r"\bdoes not work\b",
    r"\bdoesn\W?t work\b",
    r"\bnot work(ing)?\b",
    r"\bno (display|post|power)\b",
    r"\bwon\W?t (turn on|power|post)\b",
    r"\bdead\b",
    r"\bdefective\b",
    r"\bfaulty\b",
    r"\bartifact(s|ing)?\b",
    r"\bneeds repair\b",
    r"\bfor repair\b",
]

_GPU_EXCLUDES = [
    r"\blaptops?\b",
    r"\bnotebooks?\b",
    r"\bprebuilt\b",
    r"\bgaming pc\b",
    r"\bdesktop pc\b",
    r"\bbundles?\b",
    r"\bwater ?blocks?\b",
    r"\bbackplates?\b",
    r"\bshrouds?\b",
    r"\bfan only\b",
    r"\bbrackets?\b",
    r"\bcooler only\b",
]
_CPU_EXCLUDES = [
    r"\bmotherboard combo\b",
    r"\bcombo\b",
    r"\bmobo\b",
    r"\bbundles?\b",
    r"\blaptops?\b",
    r"\bcooler only\b",
]
_CONSOLE_EXCLUDES = [
    r"\bcase only\b",
    r"\bskins?\b",
    r"\bfaceplate only\b",
    r"\bshell only\b",
    r"\bstand only\b",
    r"\bcharging station\b",
    r"\b(hard drive|hdd|faceplate|shell|manual|box)( \w+)? only\b",
]

CATEGORY_EXCLUDES: dict[Category, list[str]] = {
    "gpu": _GPU_EXCLUDES,
    "cpu": _CPU_EXCLUDES,
    "console": _CONSOLE_EXCLUDES,
    "controller": _CONSOLE_EXCLUDES,
}

_NEW_IN_TITLE = re.compile(
    r"\bsealed\b|\bnew in box\b|\bnib\b|\bbnib\b|\bbrand new\b|\bfactory sealed\b"
)
_EBAY_NEW_IDS = {"1000", "1500"}
_EBAY_FOR_PARTS_ID = "7000"


def _any(patterns: Sequence[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def detect_condition(title_norm: str, condition_id: str | None) -> Condition | None:
    if condition_id is not None:
        if condition_id == _EBAY_FOR_PARTS_ID:
            return None
        return "new" if condition_id in _EBAY_NEW_IDS else "used"
    return "new" if _NEW_IN_TITLE.search(title_norm) else "used"


def match_single(
    title: str, products: Sequence[Product], condition_id: str | None = None
) -> tuple[Product, Condition] | None:
    title_norm = normalize(title)
    if _any(GLOBAL_EXCLUDES, title_norm):
        return None
    matches = [
        (p, m) for p in products if p.enabled and (m := re.search(p.match, title_norm))
    ]
    product = pick_most_specific(matches)
    if product is None:
        return None
    if _any(CATEGORY_EXCLUDES[product.category], title_norm) or _any(product.exclude, title_norm):
        return None
    condition = detect_condition(title_norm, condition_id)
    return None if condition is None else (product, condition)
