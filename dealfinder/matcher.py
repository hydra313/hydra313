from __future__ import annotations

import re
from functools import lru_cache
from collections.abc import Sequence

from dealfinder.models import Category, Condition, Product
from dealfinder.text import normalize, pick_most_specific
from dealfinder.console_editions import edition_text, match_edition, primary_title

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
    r"\bcracked (?:display|screen)\b",
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

# Apply before lot routing as well as single-item/sold matching. Edition words
# on an accessory do not identify the hardware it fits.
_NON_HARDWARE = re.compile(
    r'\b(?:fight ?sticks?|fighting sticks?|arcade (?:fight|fighting|controller|stick)|leverless|'
    r'haute ?(?:pad|42)|hitbox|racing wheel|steering wheel|flight stick|'
    r'cooling fans?|rgb fans?|controller parts?|drive bezels?|dvd drive bezels?|eject buttons?|'
    r'analog stick modules?|replacement housings?|dance pads?|guitar controllers?|drum controllers?|'
    r'flight controllers?|hotas|light guns?|replacement (?:buttons?|shells?|sticks?|parts?|ports?)|'
    r'button sets?|thumb ?grips?|thumbstick caps?|silicone (?:protective )?cases?|'
    r'protective (?:cases?|covers?)|controller protectors?|replacement shells?)\b')


@lru_cache(maxsize=32768)
def unrelated_accessory(title: str) -> bool:
    text = normalize(title)
    if re.search(r'\b(?:hp omen (?:15|16|17|18)[a-z]?|razer blade|dell xps|rog zephyrus|predator helios|msi (?:raider|stealth|titan)|thinkpad|elitebook)\b|\b\d{4,5}hx\b',text):
        return True
    if re.search(r'\b(?:e ?gpu|xg mobile|external (?:gpu|graphics)|(?:gpu|graphics card) (?:enclosure|dock)|mobile gpu)\b',text):
        return True
    if (re.search(r'\b(?:cameras?|camcorders?|dslr|vivitar|coolpix|powershot)\b',text)
            and not re.search(r'\b(?:console|playstation|xbox|nintendo)\b',text)):
        return True
    if _NON_HARDWARE.search(text) or re.search(r'\b(?:non|not|no) halo\b', text):
        return True
    console = re.search(r'\bconsoles?\b',text)
    theme = re.search(r'\b(?:halo ?[345]|halo reach|halo infinite|god of war|spider man|battlefront)\b',text)
    if (console and theme and console.start() < theme.start()
            and re.search(r'\b(?:black|white|standard|regular|chrome)\b',text)
            and not re.search(r'\b(?:limited|special|collectors?|anniversary) edition\b',text)):
        return True  # ordinary hardware bundled with a themed game/controller
    # A full gamepad can advertise programmable buttons; loose button kits cannot.
    return bool(re.search(r'\bbuttons?\b', text) and not re.search(
        r'\b(?:wireless|wired|programmable|remappable|back)\b.*\b(?:controller|gamepad)\b|'
        r'\b(?:controller|gamepad)\b.*\b(?:programmable|remappable|back) buttons?\b', text))


@lru_cache(maxsize=32768)
def category_compatible(title: str, category: str | None, kind: str = 'single') -> bool:
    text=normalize(title)
    condition_text=re.sub(r'\b(dead rising|red dead redemption|dead by daylight|dead space)\b','',text)
    if kind == 'single' and any(re.search(pattern,condition_text) for pattern in GLOBAL_EXCLUDES):
        return False
    if category in ('gpu','cpu') and kind == 'single':
        return not any(re.search(pattern,text) for pattern in CATEGORY_EXCLUDES[category])
    if category == 'console':
        if re.match(r'^(?:box|manuals?|packaging)\b',text):
            return False
        for_platform=re.search(r'\bfor (?:the )?(?:sony )?(?:playstation ?[1-5]|ps[1-5])\b',text)
        if for_platform and not re.search(r'\b(?:console|system|unit)\b',text[:for_platform.start()]):
            return False
        controller=re.search(r'\b(?:controllers?|gamepads?|dualsense|dualshock|joy ?cons?)\b',text)
        hardware=re.search(r'\b(?:consoles?|systems?|units?)\b',text)
        storage=re.search(r'\b\d+ ?(?:gb|tb)\b',text)
        if controller and not (storage or hardware and hardware.start() < controller.start()):
            return False
    return True


_NEW_IN_TITLE = re.compile(
    r"\bsealed\b|\bnew in box\b|\bnib\b|\bbnib\b|\bbrand new\b|\bfactory sealed\b"
)
_EBAY_NEW_IDS = {"1000", "1500"}
_EBAY_FOR_PARTS_ID = "7000"
_INTEL_CPU = re.compile(r"\b(intel|i[3579]|core ultra)\b")
_AMD_CPU = re.compile(r"\b(amd|ryzen)\b")


def cpu_brand_compatible(title_norm: str, query: str) -> bool:
    """Shared model numbers (e.g. Intel i7-7700 and Ryzen 7700) need a brand check.

    Keep unbranded model-only titles, but reject explicit conflicting manufacturers.
    Use the catalog query so the same rule works for generated bulk products.
    """
    query = normalize(query)
    return not ((_AMD_CPU.search(query) and _INTEL_CPU.search(title_norm))
                or (_INTEL_CPU.search(query) and _AMD_CPU.search(title_norm)))


def _any(patterns: Sequence[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def detect_condition(title_norm: str, condition_id: str | None) -> Condition | None:
    if condition_id == _EBAY_FOR_PARTS_ID:
        return None
    if re.search(r'(?<!not )\b(?:used|pre owned|preowned)\b', title_norm):
        return "used"
    if condition_id is not None:
        return "new" if condition_id in _EBAY_NEW_IDS else "used"
    return "new" if _NEW_IN_TITLE.search(title_norm) else "used"


def match_single(
    title: str, products: Sequence[Product], condition_id: str | None = None
) -> tuple[Product, Condition] | None:
    title_norm = normalize(title)
    if unrelated_accessory(title):
        return None
    condition_text = re.sub(r'\b(dead rising|red dead redemption|dead by daylight|dead space)\b',
                            '', title_norm)
    if _any(GLOBAL_EXCLUDES, condition_text):
        return None
    matches = [
        (p, m) for p in products if p.enabled and not p.required_tokens
        and category_compatible(title,p.category)
        and (p.category != "cpu" or cpu_brand_compatible(title_norm, p.ebay_query))
        and (m := re.search(p.match, title_norm))
    ]
    # Curated patterns retain their stable IDs and overrides. Reference editions
    # are a fallback, with a separate hardware identity and accessory check.
    if not matches:
        head = primary_title(edition_text(title))
        tokens = set(re.findall(r'\w+', head))
        matches = [(p, m) for p in products if p.required_tokens and category_compatible(title,p.category)
                   and (m := match_edition(p, head, tokens))]
        matches = [(p, m) for p, m in matches if not any(
            p.match == q.match and set(p.required_tokens) < set(q.required_tokens)
            for q, _ in matches)]
    product = pick_most_specific(matches)
    if product is None or not product.enabled:
        return None
    if _any(CATEGORY_EXCLUDES[product.category], title_norm) or _any(product.exclude, title_norm):
        return None
    condition = detect_condition(title_norm, condition_id)
    return None if condition is None else (product, condition)
