from __future__ import annotations

import base64
import io
import json
import logging

import anthropic
from PIL import Image, ImageOps

from dealfinder.config import Config
from dealfinder.models import ScanItem, ScanResult
from dealfinder.sources.base import HttpClient, SourceError

log = logging.getLogger(__name__)

MAX_PHOTOS = 8
MAX_EDGE = 1568
MAX_TOKENS = 16000
BETAS = ["server-side-fallback-2026-07-01"]
MAX_ITEM_QUANTITY = 50  # a larger count is a misreading, not a lot
UNKNOWN_MODEL_PRICES = (10.0, 50.0)  # USD per million input/output tokens, at least

# Spec 11.4, strict for structured outputs: every object closes its properties
# and requires all of them; nullable fields use type arrays.
SCAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["game", "console", "controller", "accessory", "other"],
                    },
                    "platform": {"type": "string"},
                    "title": {"type": "string"},
                    "edition": {"type": ["string", "null"]},
                    "completeness": {
                        "type": "string",
                        "enum": ["loose", "cib", "sealed", "unknown"],
                    },
                    "quantity": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "photo_index": {"type": "integer"},
                },
                "required": [
                    "kind", "platform", "title", "edition", "completeness",
                    "quantity", "confidence", "photo_index",
                ],
                "additionalProperties": False,
            },
        },
        "unit_count_estimate": {"type": ["integer", "null"]},
        "notes": {"type": "string"},
    },
    "required": ["items", "unit_count_estimate", "notes"],
    "additionalProperties": False,
}

PROMPT = """\
These photos are from an online auction or resale listing. Identify every video \
game, game console and controller visible in them.

Rules:
- Report only what is readable in the photos: a title must be legible on a \
label, spine, cartridge or disc. Never guess a title from cover art, colors or \
shapes alone; if you cannot read it, leave the item out or describe it with \
confidence "low".
- Flag special and limited editions (collector's, limited, special or \
anniversary editions, special-color consoles) in "edition"; use null otherwise.
- "completeness": "loose" for a bare cartridge, disc or device, "cib" when the \
box and manual are present, "sealed" for factory-sealed, "unknown" when the \
photos do not show it.
- "quantity" is the number of identical copies of that item you can see. \
Count each physical copy once, even when it appears in several photos.
- "photo_index" is the 0-based position of the photo that shows the item best, \
in the order the photos were given.
- "confidence": "high" when the title and platform are clearly readable, \
"medium" when mostly readable, "low" when uncertain.
- "unit_count_estimate": if the listing title states no count, count the \
total number of units (games, consoles, controllers and accessories) visible \
in the photos; use null when the title already gives a count.
- Put anything else worth knowing (damage, duplicates across photos, items \
you could not read) in "notes".

Listing title: {title}"""


class ScanDeferred(Exception):
    """Rate-limited; try again on a later refresh. Nothing is recorded."""


def resize_jpeg(data: bytes, max_edge: int = MAX_EDGE) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        width, height = img.size
        scale = max_edge / max(width, height)
        if scale < 1:
            img = img.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.LANCZOS,
            )
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()


def compute_cost(
    response, requested_model: str, prices: dict[str, tuple[float, float]],
) -> tuple[int, int, float]:
    usage = getattr(response, "usage", None)
    iterations = getattr(usage, "iterations", None)
    if iterations:
        entries = [
            (getattr(it, "model", None) or requested_model,
             getattr(it, "input_tokens", 0) or 0,
             getattr(it, "output_tokens", 0) or 0)
            for it in iterations
        ]
    else:
        entries = [(
            getattr(response, "model", None) or requested_model,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
        )]
    # A model missing from the table is priced at the highest configured rate, and
    # at no less than UNKNOWN_MODEL_PRICES, so unknown fallback usage never
    # under-counts spend.
    highest = (max(UNKNOWN_MODEL_PRICES[0], *(p[0] for p in prices.values())),
               max(UNKNOWN_MODEL_PRICES[1], *(p[1] for p in prices.values())))
    total_in = total_out = 0
    cost = 0.0
    for model, inp, out in entries:
        in_rate, out_rate = prices.get(model, highest)
        total_in += inp
        total_out += out
        cost += (inp * in_rate + out * out_rate) / 1_000_000
    return total_in, total_out, cost


def _item(raw: dict, sent: list[int]) -> ScanItem:
    """`sent` holds the image_urls positions of the photos actually sent, so the
    model's index into the sent photos maps back to the listing's photo; an
    out-of-range index becomes -1 (unknown photo)."""
    raw_index = int(raw["photo_index"])
    return ScanItem(
        kind=raw["kind"],
        platform=raw["platform"],
        title=raw["title"],
        edition=raw["edition"],
        completeness=raw["completeness"],
        quantity=min(MAX_ITEM_QUANTITY, max(1, int(raw["quantity"]))),
        confidence=raw["confidence"],
        photo_index=sent[raw_index] if 0 <= raw_index < len(sent) else -1,
    )


class VisionScanner:
    def __init__(self, client, http: HttpClient, cfg: Config) -> None:
        self._client = client
        self._http = http
        self._cfg = cfg

    def _failed(self, error: str, model: str | None = None, input_tokens: int = 0,
                output_tokens: int = 0, cost: float = 0.0) -> ScanResult:
        return ScanResult(
            status="failed", items=[], unit_count_estimate=None, notes="",
            model=model or self._cfg.vision_model, input_tokens=input_tokens,
            output_tokens=output_tokens, cost_usd=cost, error=error,
        )

    def _photo_blocks(self, image_urls: list[str]) -> tuple[list[dict], list[int]]:
        """Image blocks plus the image_urls positions of the photos they hold."""
        blocks: list[dict] = []
        sent: list[int] = []
        for index, url in enumerate(image_urls[:MAX_PHOTOS]):
            try:
                data = resize_jpeg(self._http.request("GET", url).content)
            except (SourceError, OSError, ValueError, Image.DecompressionBombError) as e:
                log.info("skipping photo %s: %s", url, e)
                continue
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            })
            sent.append(index)
        return blocks, sent

    def scan(self, image_urls: list[str], title: str) -> ScanResult:
        images, sent = self._photo_blocks(image_urls)
        if not images:
            return self._failed("no usable photos")
        model = self._cfg.vision_model
        try:
            response = self._client.beta.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                betas=BETAS,
                fallbacks="default",
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": SCAN_SCHEMA},
                },
                messages=[{
                    "role": "user",
                    "content": [*images, {"type": "text", "text": PROMPT.format(title=title)}],
                }],
            )
        except anthropic.RateLimitError as e:
            raise ScanDeferred(str(e)) from e
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            return self._failed(str(e))
        except anthropic.APIError as e:
            return self._failed(str(e))

        input_tokens, output_tokens, cost = compute_cost(
            response, model, self._cfg.vision_prices)
        served = getattr(response, "model", None) or model
        if getattr(response, "stop_reason", None) == "refusal":
            return ScanResult(
                status="refused", items=[], unit_count_estimate=None, notes="",
                model=served, input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=cost, error="the model declined to scan these photos",
            )
        text = next(
            (getattr(b, "text", "") for b in getattr(response, "content", None) or []
             if getattr(b, "type", None) == "text"),
            None,
        )
        try:
            if text is None:
                raise ValueError("no text block in the response")
            data = json.loads(text)
            items = [_item(raw, sent) for raw in data["items"]]
            unit_count = data["unit_count_estimate"]
            result = ScanResult(
                status="ok", items=items,
                unit_count_estimate=None if unit_count is None else int(unit_count),
                notes=str(data["notes"]), model=served, input_tokens=input_tokens,
                output_tokens=output_tokens, cost_usd=cost,
            )
        except (ValueError, KeyError, TypeError) as e:
            return self._failed(f"unreadable scan response: {e}", served,
                                input_tokens, output_tokens, cost)
        return result
