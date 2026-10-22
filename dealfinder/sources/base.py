from __future__ import annotations

import time
from typing import Callable, Protocol
from urllib.parse import urlsplit

import httpx

from dealfinder.models import Listing

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
RETRY_WAIT_S = 2.0
BLOCK_MARKERS = ("captcha", "pardon our interruption", "cf-chl")


class SourceError(Exception):
    pass


class SourceBlocked(SourceError):
    pass


def is_bot_block(resp: httpx.Response) -> bool:
    if resp.status_code == 403:
        return True
    if "html" not in resp.headers.get("content-type", "").lower():
        return False
    text = resp.text.lower()
    return any(marker in text for marker in BLOCK_MARKERS)


class Source(Protocol):
    name: str

    def search(self, query: str) -> list[Listing]: ...

    def photos(self, source_id: str, known: list[str]) -> list[str]: ...


class HttpClient:
    def __init__(
        self,
        min_interval_s: float = 2.0,
        timeout_s: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval_s = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            timeout=timeout_s,
            transport=transport,
            headers={"User-Agent": USER_AGENT},
        )

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self._min_interval_s - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _send(self, method: str, url: str, host: str, **kwargs) -> httpx.Response | None:
        """One throttled attempt; None means a retryable failure (network error or 5xx)."""
        self._throttle(host)
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.TransportError:
            return None
        if resp.status_code >= 500:
            return None
        return resp

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = urlsplit(url).netloc
        resp = self._send(method, url, host, **kwargs)
        if resp is None:
            self._sleep(RETRY_WAIT_S)
            resp = self._send(method, url, host, **kwargs)
        if resp is None:
            raise SourceError(f"{method} {url} failed after retry")
        if resp.status_code == 429 or is_bot_block(resp):
            raise SourceBlocked(f"{method} {url} blocked (HTTP {resp.status_code})")
        if not 200 <= resp.status_code < 300:
            raise SourceError(f"{method} {url} returned HTTP {resp.status_code}")
        return resp
