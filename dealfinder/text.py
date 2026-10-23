from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")

LOT_WORDS = ("lot", "lots", "box of", "collection", "bulk", "wholesale")

# Separators become spaces, except inside all-digit tokens ("1/2", "3-5")
# and thousands-separated numbers ("1,200").
_SEPARATORS = re.compile(r"\b\d+(?:[-/]\d+)+\b|\b\d{1,3}(?:,\d{3})+\b|[-/,|™®]")
_WITH = re.compile(r"\bw/(o\b)?")
_GPU_PREFIX = re.compile(r"\b(rtx|gtx|rx)(?=\d)")
_GPU_SUFFIX = re.compile(r"(?<=\d)(xtx|xt|gre|ti|super)(?=super\b|\b)")
_TI_SUPER = re.compile(r"\bti(?=super\b)")

_BUILTIN_MASKS = (
    r"pci ?(e|express)?( ?(gen ?)?\d(\.\d)?)? ?(x ?\d+|\d+ ?x)\b",
    r"\b\d+ ?x ?\d+ ?pin\b",
    r"\b\d+ ?(gb|tb|mb|mhz|ghz|hz|w|watt|pin|bit)\b",
    r"dualshock ?\d",
    r"\bryzen ?[3579]\b",
    r"\bcore ?(ultra ?)?[3579]\b",
    r"\b32x\b",
    # GPU cooler names with a fan count ("Ventus 3X", "Windforce 3X OC")
    r"\b(ventus|windforce|eagle|gaming|twin|dual|aorus|tuf|trinity)( \w+)? \d ?x\b",
)

NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen"
        " fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
        start=1,
    )
}
_WORD = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_UNITS = (
    "consoles|systems|controllers|games|carts|cartridges|discs|units|pcs|pieces"
    "|gpus|cpus|cards|processors"
)
_NOT_A_COUNT = r"(?! ?(hdmi|dp|displayport|fans?|pin|usb|slots?))"

# Each form captures the count in group 1. Lookaheads keep matches short so
# finditer can still see a later count when an earlier hit is out of range.
_UNIT_FORM = re.compile(rf"\b(\d{{1,3}}|{_WORD})\b(?=(?: \S+){{0,3}}? (?:{_UNITS})\b)")
_FORMS = tuple(
    re.compile(p) if isinstance(p, str) else p
    for p in (
        rf"\blot of (\d+|{_WORD})\b",
        r"\bqty:? ?(\d+)\b",
        r"\((\d{1,3})\)",
        r"\b(\d{1,3}) ?pcs?\b",
        _UNIT_FORM,
        rf"\b(\d{{1,3}}) ?x\b{_NOT_A_COUNT}",
        rf"\bx ?(\d{{1,3}})\b{_NOT_A_COUNT}",
    )
)
# A unit count right after one of these is an included accessory
# ("console with 2 controllers"), not the lot size.
_ACCESSORY_LEAD = re.compile(r"(?:^|\s)(with|w|\+|plus|and|includes|including) $")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_LOT_WORD = re.compile(r"\b(" + "|".join(re.escape(w) for w in LOT_WORDS) + r")\b")


def normalize(title: str) -> str:
    s = _WITH.sub(lambda m: " without " if m[1] else " with ", title.lower().replace("&", " and "))
    s = _SEPARATORS.sub(lambda m: m[0] if m[0][0].isdigit() else " ", s)
    s = _GPU_PREFIX.sub(r"\1 ", s)
    s = _GPU_SUFFIX.sub(r" \1", s)
    s = _TI_SUPER.sub("ti ", s)
    return " ".join(s.split())


def mask(text: str, patterns: Iterable[str]) -> str:
    """Replace the union of all match spans of `patterns` with "_"."""
    spans = sorted(
        m.span()
        for p in patterns
        for m in re.finditer(p, text)
        if m.end() > m.start()
    )
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, pos = [], 0
    for start, end in merged:
        out.append(text[pos:start])
        out.append(" _ ")
        pos = end
    out.append(text[pos:])
    return " ".join("".join(out).split())


def quantity_mask(title_norm: str, mask_patterns: Iterable[str] = ()) -> str:
    """The title parse_quantity reads counts from: thousands separators dropped,
    built-in model/spec masks and `mask_patterns` (platform names) masked."""
    return mask(_THOUSANDS.sub("", title_norm), (*_BUILTIN_MASKS, *mask_patterns))


def parse_quantity(title_norm: str, mask_patterns: Iterable[str] = ()) -> int | None:
    masked = quantity_mask(title_norm, mask_patterns)
    for form in _FORMS:
        for m in form.finditer(masked):
            if form is _UNIT_FORM and _ACCESSORY_LEAD.search(masked, 0, m.start()):
                continue
            raw = m.group(1)
            n = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
            if 2 <= n <= 200:
                return n
    if _LOT_WORD.search(title_norm):
        return None
    return 1


def pick_most_specific(matches: list[tuple[T, re.Match]]) -> T | None:
    winners: list[T] = []
    for obj, m in matches:
        contains_all = all(
            m.start() <= other.start() and other.end() <= m.end()
            for other_obj, other in matches
            if other_obj != obj
        )
        if contains_all and obj not in winners:
            winners.append(obj)
    return winners[0] if len(winners) == 1 else None
