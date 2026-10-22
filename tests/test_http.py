import httpx
import pytest
import respx

from dealfinder.sources.base import HttpClient, SourceBlocked, SourceError, is_bot_block


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(ft):
    return HttpClient(sleep=ft.sleep, clock=ft.clock)


@respx.mock
def test_throttles_same_host():
    ft = FakeTime()
    respx.get("https://example.com/a").respond(200, json={})
    respx.get("https://example.com/b").respond(200, json={})
    respx.get("https://other.test/c").respond(200, json={})
    client = make_client(ft)
    client.request("GET", "https://example.com/a")
    assert ft.sleeps == []
    client.request("GET", "https://example.com/b")
    assert ft.sleeps == [2.0]
    client.request("GET", "https://other.test/c")
    assert ft.sleeps == [2.0]


@respx.mock
def test_throttle_counts_elapsed_time():
    ft = FakeTime()
    respx.get("https://example.com/a").respond(200, json={})
    client = make_client(ft)
    client.request("GET", "https://example.com/a")
    ft.now += 1.5
    client.request("GET", "https://example.com/a")
    assert ft.sleeps == [pytest.approx(0.5)]


@respx.mock
def test_retries_once_on_500():
    ft = FakeTime()
    route = respx.get("https://example.com/a").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    resp = make_client(ft).request("GET", "https://example.com/a")
    assert resp.json() == {"ok": True}
    assert route.call_count == 2
    assert 2.0 in ft.sleeps


@respx.mock
def test_second_500_raises_source_error():
    ft = FakeTime()
    route = respx.get("https://example.com/a").respond(503)
    with pytest.raises(SourceError) as exc:
        make_client(ft).request("GET", "https://example.com/a")
    assert not isinstance(exc.value, SourceBlocked)
    assert route.call_count == 2


@respx.mock
def test_retries_once_on_transport_error():
    ft = FakeTime()
    route = respx.get("https://example.com/a").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={})]
    )
    make_client(ft).request("GET", "https://example.com/a")
    assert route.call_count == 2


@respx.mock
def test_429_raises_blocked():
    ft = FakeTime()
    route = respx.get("https://example.com/a").respond(429)
    with pytest.raises(SourceBlocked):
        make_client(ft).request("GET", "https://example.com/a")
    assert route.call_count == 1


@respx.mock
def test_captcha_page_raises_blocked():
    ft = FakeTime()
    respx.get("https://example.com/a").respond(
        200, html="<html><body>Please solve this CAPTCHA</body></html>"
    )
    with pytest.raises(SourceBlocked):
        make_client(ft).request("GET", "https://example.com/a")


@respx.mock
def test_other_4xx_raises_source_error():
    ft = FakeTime()
    respx.get("https://example.com/a").respond(404)
    with pytest.raises(SourceError) as exc:
        make_client(ft).request("GET", "https://example.com/a")
    assert not isinstance(exc.value, SourceBlocked)


@respx.mock
def test_sends_browser_user_agent():
    ft = FakeTime()
    route = respx.get("https://example.com/a").respond(200, json={})
    make_client(ft).request("GET", "https://example.com/a")
    assert "Mozilla/5.0" in route.calls[0].request.headers["user-agent"]


@pytest.mark.parametrize(
    "resp,expected",
    [
        (httpx.Response(403), True),
        (httpx.Response(200, html="<p>Pardon Our Interruption</p>"), True),
        (httpx.Response(200, html='<script src="/cdn-cgi/cf-chl-bypass"></script>'), True),
        (httpx.Response(200, html="<p>normal page</p>"), False),
        (httpx.Response(200, json={"note": "captcha mentioned in json"}), False),
    ],
)
def test_is_bot_block(resp, expected):
    assert is_bot_block(resp) is expected
