import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from PIL import Image

from dealfinder.config import Config
from dealfinder.models import ScanItem
from dealfinder.sources.base import HttpClient
from dealfinder.vision import (
    PROMPT,
    SCAN_SCHEMA,
    ScanDeferred,
    VisionScanner,
    compute_cost,
    resize_jpeg,
)

FIXTURES = Path(__file__).parent / "fixtures" / "vision"
OK_JSON = (FIXTURES / "ok.json").read_text()
PHOTO = (FIXTURES / "photo.jpg").read_bytes()
URLS = ["https://img.example.com/1.jpg", "https://img.example.com/2.jpg"]
API_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def fake_client(response=None, error=None):
    messages = FakeMessages(response, error)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def make_response(text=OK_JSON, stop_reason="end_turn", input_tokens=10_000,
                  output_tokens=2_000, iterations=None, model="claude-opus-5-5"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                            iterations=iterations)
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage, model=model)


def make_http(photos=None):
    photos = {u: PHOTO for u in URLS} if photos is None else photos

    def handler(request):
        body = photos.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "image/jpeg"})

    return HttpClient(transport=httpx.MockTransport(handler), sleep=lambda s: None,
                      clock=lambda: 0.0)


def make_scanner(response=None, error=None, photos=None):
    client, messages = fake_client(response, error)
    return VisionScanner(client, make_http(photos), Config()), messages


def jpeg(width, height, mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, (width, height)).save(buf, "PNG" if mode == "RGBA" else "JPEG")
    return buf.getvalue()


def api_status_error(cls, status):
    return cls(f"HTTP {status}", response=httpx.Response(status, request=API_REQUEST), body=None)


def test_request_arguments():
    scanner, messages = make_scanner(make_response())
    scanner.scan(URLS, "N64 lot with controllers")
    [kwargs] = messages.calls
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["max_tokens"] == 16000
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["fallbacks"] == "default"
    assert kwargs["output_config"]["effort"] == "medium"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["output_config"]["format"]["schema"] is SCAN_SCHEMA
    for banned in ("thinking", "temperature", "top_p", "top_k", "stream"):
        assert banned not in kwargs
    [message] = kwargs["messages"]
    assert message["role"] == "user"
    images, text = message["content"][:2], message["content"][2]
    for block in images:
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/jpeg"
        assert "\n" not in block["source"]["data"]
        Image.open(io.BytesIO(base64.b64decode(block["source"]["data"]))).verify()
    assert text["type"] == "text"
    assert "N64 lot with controllers" in text["text"]


def test_parses_items_and_cost():
    scanner, _ = make_scanner(make_response())
    result = scanner.scan(URLS, "N64 lot")
    assert result.status == "ok"
    assert result.error is None
    assert result.items == [
        ScanItem(kind="game", platform="Nintendo 64",
                 title="The Legend of Zelda: Ocarina of Time", edition="Collector's Edition",
                 completeness="loose", quantity=1, confidence="high", photo_index=0),
        ScanItem(kind="controller", platform="Nintendo 64", title="Nintendo 64 Controller",
                 edition=None, completeness="unknown", quantity=2, confidence="medium",
                 photo_index=1),
    ]
    assert result.unit_count_estimate == 3
    assert result.notes == "Two controllers and one cartridge visible."
    assert result.model == "claude-opus-5-5"
    assert (result.input_tokens, result.output_tokens) == (10_000, 2_000)
    assert result.cost_usd == pytest.approx(0.08)


def test_cost_sums_iterations_and_prices_unknown_model_high():
    prices = {"claude-opus-5-5": (4.0, 20.0), "cheap-model": (1.0, 5.0)}
    response = make_response(
        input_tokens=1, output_tokens=1,
        iterations=[
            SimpleNamespace(input_tokens=10_000, output_tokens=1_000, model="claude-opus-5-5"),
            SimpleNamespace(input_tokens=20_000, output_tokens=3_000, model="mystery-model"),
            SimpleNamespace(input_tokens=5_000, output_tokens=500),  # no model: requested
        ])
    inp, out, cost = compute_cost(response, "claude-opus-5-5", prices)
    assert (inp, out) == (35_000, 4_500)
    # 0.04 + 0.02 | unknown model at the highest rate: 0.08 + 0.06 | 0.02 + 0.01
    assert cost == pytest.approx(0.23)


def test_cost_without_iterations_uses_top_level_usage():
    response = make_response(iterations=[])
    assert compute_cost(response, "claude-opus-5-5", {"claude-opus-5-5": (4.0, 20.0)}) == (
        10_000, 2_000, pytest.approx(0.08))
    bare = SimpleNamespace(usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0))
    assert compute_cost(bare, "claude-opus-5-5", {"claude-opus-5-5": (4.0, 20.0)})[2] == (
        pytest.approx(4.0))


def test_refusal_status_keeps_cost():
    scanner, _ = make_scanner(make_response(text=None, stop_reason="refusal"))
    result = scanner.scan(URLS, "lot")
    assert result.status == "refused"
    assert result.items == []
    assert result.cost_usd == pytest.approx(0.08)
    assert (result.input_tokens, result.output_tokens) == (10_000, 2_000)


def test_invalid_json_failed_with_cost():
    scanner, _ = make_scanner(make_response(text="{not json"))
    result = scanner.scan(URLS, "lot")
    assert result.status == "failed"
    assert result.error
    assert result.cost_usd == pytest.approx(0.08)


def test_rate_limit_raises_deferred():
    scanner, _ = make_scanner(error=api_status_error(anthropic.RateLimitError, 429))
    with pytest.raises(ScanDeferred):
        scanner.scan(URLS, "lot")


def test_api_error_failed_zero_cost():
    scanner, _ = make_scanner(error=api_status_error(anthropic.InternalServerError, 500))
    result = scanner.scan(URLS, "lot")
    assert result.status == "failed"
    assert result.cost_usd == 0
    assert (result.input_tokens, result.output_tokens) == (0, 0)
    assert "HTTP 500" in result.error


def test_connection_error_failed_zero_cost():
    scanner, _ = make_scanner(error=anthropic.APIConnectionError(request=API_REQUEST))
    result = scanner.scan(URLS, "lot")
    assert result.status == "failed"
    assert result.cost_usd == 0
    assert result.error == "Connection error."


def test_unknown_api_error_failed_zero_cost():
    error = anthropic.APIResponseValidationError(
        response=httpx.Response(200, request=API_REQUEST), body=None)
    scanner, _ = make_scanner(error=error)
    result = scanner.scan(URLS, "lot")
    assert result.status == "failed"
    assert result.cost_usd == 0
    assert result.error == "Data returned by API invalid for expected schema."


def one_item_json(photo_index=0, quantity=1):
    return json.dumps({
        "items": [{
            "kind": "game", "platform": "SNES", "title": "Chrono Trigger", "edition": None,
            "completeness": "loose", "quantity": quantity, "confidence": "high",
            "photo_index": photo_index,
        }],
        "unit_count_estimate": None,
        "notes": "",
    })


def test_photo_index_maps_back_to_original_url_position():
    # first URL 404s, so the only photo sent is image_urls[1]
    scanner, _ = make_scanner(make_response(text=one_item_json(photo_index=0)),
                              photos={URLS[1]: PHOTO})
    result = scanner.scan(URLS, "lot")
    assert result.items[0].photo_index == 1


def test_photo_index_out_of_range_is_unknown():
    scanner, _ = make_scanner(make_response(text=one_item_json(photo_index=5)))
    result = scanner.scan(URLS, "lot")
    assert result.items[0].photo_index == -1
    scanner, _ = make_scanner(make_response(text=one_item_json(photo_index=-2)))
    assert scanner.scan(URLS, "lot").items[0].photo_index == -1


def test_quantity_clamped_to_at_least_one():
    scanner, _ = make_scanner(make_response(text=one_item_json(quantity=0)))
    assert scanner.scan(URLS, "lot").items[0].quantity == 1


def test_no_photos_failed_without_request():
    scanner, messages = make_scanner(make_response(), photos={})
    result = scanner.scan(URLS, "lot")
    assert result.status == "failed"
    assert result.error == "no usable photos"
    assert result.cost_usd == 0
    assert messages.calls == []


def test_failed_and_unreadable_photos_are_skipped():
    photos = {URLS[0]: PHOTO, URLS[1]: b"not an image"}
    scanner, messages = make_scanner(make_response(), photos=photos)
    scanner.scan(URLS + ["https://img.example.com/missing.jpg"], "lot")
    content = messages.calls[0]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "text"]


def test_at_most_eight_photos():
    urls = [f"https://img.example.com/{i}.jpg" for i in range(10)]
    scanner, messages = make_scanner(make_response(), photos={u: PHOTO for u in urls})
    scanner.scan(urls, "lot")
    content = messages.calls[0]["messages"][0]["content"]
    assert sum(b["type"] == "image" for b in content) == 8


def test_resize_caps_long_edge():
    out = Image.open(io.BytesIO(resize_jpeg(jpeg(3000, 1000))))
    assert out.format == "JPEG"
    assert out.size == (1568, 523)
    tall = Image.open(io.BytesIO(resize_jpeg(jpeg(1000, 3000))))
    assert tall.size == (523, 1568)


def test_resize_never_upscales():
    out = Image.open(io.BytesIO(resize_jpeg(jpeg(800, 600))))
    assert out.size == (800, 600)


def test_resize_converts_to_rgb():
    out = Image.open(io.BytesIO(resize_jpeg(jpeg(40, 30, mode="RGBA"))))
    assert (out.format, out.mode) == ("JPEG", "RGB")


def _objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)


def test_schema_is_strict():
    objects = list(_objects(SCAN_SCHEMA))
    assert len(objects) == 2
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])
    item = SCAN_SCHEMA["properties"]["items"]["items"]["properties"]
    assert item["kind"]["enum"] == ["game", "console", "controller", "accessory", "other"]
    assert item["completeness"]["enum"] == ["loose", "cib", "sealed", "unknown"]
    assert item["confidence"]["enum"] == ["high", "medium", "low"]
    assert item["edition"]["type"] == ["string", "null"]
    assert SCAN_SCHEMA["properties"]["unit_count_estimate"]["type"] == ["integer", "null"]


def test_prompt_instructions():
    lower = PROMPT.lower()
    for phrase in ("game", "console", "controller", "readable", "limited", "count"):
        assert phrase in lower
