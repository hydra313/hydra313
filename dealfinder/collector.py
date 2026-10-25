"""Built-in public search collector; sign-in stays in a separate user-owned window.

Each site has an app-owned persistent profile. Nothing attaches to ordinary user
tabs, reads passwords, exports cookies, or attempts to solve verification.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

SITES = ("ebay", "hibid")
SIGNIN = {"ebay": "https://www.ebay.com/signin/", "hibid": "https://hibid.com/login"}
CAPTURE_JS = Path(__file__).with_name("collector_capture.js")


def valid_job(job: dict) -> bool:
    try:
        source, query, kind = job["source"], job["query"], job.get("kind", "active")
        url = urlsplit(job["url"])
        params = parse_qs(url.query)
        if (url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
                or not isinstance(query, str) or not 0 < len(query) <= 200):
            return False
        if source == "ebay":
            return (url.hostname == "www.ebay.com" and url.path == "/sch/i.html"
                    and params.get("_nkw") == [query] and kind in ("active", "sold")
                    and (params.get("LH_Sold") == ["1"] if kind == "sold" else "LH_Sold" not in params))
        return (source == "hibid" and kind == "active" and url.hostname == "hibid.com"
                and url.path == "/lots" and params.get("q") == [query])
    except (KeyError, TypeError, ValueError):
        return False


def capture_problem(capture: dict, site: str) -> str:
    url = urlsplit(capture.get("url", ""))
    if (url.path not in ("/sch/i.html", "/lots") or
            re.search(r"pardon our interruption|verify|captcha|security measure|just a moment|"
                      r"attention required|access denied|sign in|sign-in", capture.get("title", ""), re.I)):
        return "Sign-in or verification required. Open the site to sign in or verify, then continue scanning."
    if not capture.get("count") and not re.search(
            r"\b0 results\b|no exact matches found|no lots found|no results found|\b0 lots\b",
            capture.get("text", ""), re.I):
        return "No readable search results. Open the site to check access, then continue scanning."
    return ""


class BackgroundCollector:
    def __init__(self, base_url: str, profile_dir: Path, token: str, *, interval: float = 30):
        self.base_url, self.profile_dir, self._token = base_url, Path(profile_dir), token
        self.interval = interval
        self._allow_sold = True
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._commands: queue.Queue = queue.Queue(maxsize=16)
        self._thread: threading.Thread | None = None
        self._sites = {site: {"state": "starting", "message": "Waiting for the first search"} for site in SITES}

    def update(self, site, **values):
        with self._lock:
            self._sites[site].update(values)

    def snapshot(self):
        with self._lock:
            return {"running": bool(self._thread and self._thread.is_alive()),
                    "sites": {k: dict(v) for k, v in self._sites.items()}}

    def available_sites(self):
        with self._lock:
            return [s for s in SITES if self._sites[s]["state"] not in ("needs_attention", "signing_in")]

    def command(self, action, site):
        if action not in ("signin", "resume") or site not in SITES:
            raise ValueError("Unknown scanner command")
        try:
            self._commands.put_nowait((action, site))
        except queue.Full:
            raise ValueError("Please wait for the previous scanner command") from None

    def next_command(self):
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def start(self):
        if not self._thread or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="background-collector", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as browser:
                self._collect(browser)
        except Exception:
            # Browser exception strings can include session URLs; do not log them.
            for site in SITES:
                self.update(site, state="needs_attention", message="Background browser could not start. Restart ConsoleHawk; check that Google Chrome is installed.")

    def _collect(self, browser):
        contexts, pages = {}, {}
        headers = {"X-Dealfinder-Token": self._token, "X-ConsoleHawk-Collector": "background"}
        script = CAPTURE_JS.read_text(encoding="utf-8")

        def close_site(site):
            context = contexts.pop(site, None)
            pages.pop(site, None)
            if context:
                try:
                    context.close()
                except Exception:
                    pass

        def open_site(site, headed=False):
            context = browser.chromium.launch_persistent_context(
                str(self.profile_dir / "chrome" / site), channel="chrome" if sys.platform == "win32" else "chromium",
                headless=not headed, chromium_sandbox=True, accept_downloads=False,
                viewport={"width": 1440, "height": 1000}, locale="en-US")
            contexts[site] = context
            page = context.pages[0] if context.pages else context.new_page()
            pages[site] = page
            if not headed:
                context.on("page", lambda popup: popup.close() if popup != page else None)
            return page

        with httpx.Client(base_url=self.base_url, headers=headers, timeout=65,
                          follow_redirects=False, trust_env=False) as api:
            def post(path, data):
                response = api.post(path, json=data)
                response.raise_for_status()
                return response.json()

            try:
                while not self._stop.is_set():
                    command = self.next_command()
                    if command:
                        action, site = command
                        close_site(site)
                        if action == "signin":
                            try:
                                page = open_site(site, headed=True)
                                self.update(site, state="signing_in", message="Finish sign-in in the site window. Then return here and continue scanning.")
                                page.goto(SIGNIN[site], wait_until="domcontentloaded", timeout=45000)
                            except Exception:
                                self.update(site, state="needs_attention", message="Sign-in window closed or could not load. Click Sign in to try again.")
                        else:
                            if site == "ebay":
                                self._allow_sold = True
                                self.update(site, sold_message="")
                            self.update(site, state="starting", message="Resuming in the background")
                        continue
                    sites = self.available_sites()
                    if not sites:
                        self._stop.wait(1)
                        continue
                    try:
                        job = post("/browser/next", {"sites": sites, "allow_sold": self._allow_sold})
                    except httpx.HTTPError:
                        self._stop.wait(5)
                        continue
                    if job.get("paused"):
                        self._stop.wait(2)
                        continue
                    if not valid_job(job):
                        self._stop.wait(self.interval)
                        continue
                    site = job["source"]
                    self.update(site, state="scanning", message="Scanning in the background", query=job["query"])
                    try:
                        page = pages.get(site) or open_site(site)
                        page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
                        try:
                            page.wait_for_function("""() => document.querySelector('li.s-card,li.s-item,.app-auction-lot-card') ||
                                /pardon our interruption|verify|captcha|just a moment|sign in|access denied/i.test(document.title+' '+(document.querySelector('h1')?.innerText||'')) ||
                                /0 results|no lots found|no results found|no exact matches found/i.test(document.body?.innerText||'')""", timeout=25000)
                        except Exception:
                            pass
                        capture = page.evaluate(script, site)
                        problem = capture_problem(capture, site)
                        if not valid_job({**job, "url": capture["url"]}):
                            problem = "Sign-in or verification required. Open the site to sign in or verify, then continue scanning."
                        if problem:
                            raise ValueError(problem)
                        params = parse_qs(urlsplit(job["url"]).query)
                        result = post(f"/browser/{site}/results", {
                            "query": job["query"], "kind": job.get("kind", "active"),
                            "html": capture["html"], "has_next": capture.get("has_next") is True, "captured_at": datetime.now(timezone.utc).isoformat(),
                            "page": int(params.get("_pgn", ["1"])[0]), "auction": params.get("LH_Auction") == ["1"]})
                        self.update(site, state="ready", message=f"Background scan complete · {result.get('usable', 0)} usable results",
                                    last_capture=datetime.now(timezone.utc).isoformat())
                    except Exception:
                        message = "Search needs attention. Open the site to check access, then continue scanning."
                        if site == "ebay" and job.get("kind") == "sold":
                            self._allow_sold = False
                            self.update(site, state="ready", message="Public listing scans continue",
                                        sold_message="Sold prices unavailable. Sign in and continue scanning to retry; public listings do not require this step.")
                        else:
                            self.update(site, state="needs_attention", message=message)
                        close_site(site)
                        try:
                            post(f"/browser/{site}/results", {"query": job["query"], "error": message,
                                 "captured_at": datetime.now(timezone.utc).isoformat()})
                        except httpx.HTTPError:
                            pass
                    # Interruptible delay: commands and shutdown never wait a full scan interval.
                    for _ in range(max(1, int(self.interval))):
                        if self._stop.wait(1) or not self._commands.empty():
                            break
            finally:
                for site in list(contexts):
                    close_site(site)
