from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

BLOCK_MARKERS = (
    "pardon our interruption",
    "splashui/challenge",
    "captcha",
    "cf-chl",
    "attention required",
    "access denied",
)


class SourceError(RuntimeError):
    pass


class Blocked(SourceError):
    """Bot protection answered instead of the site. Retry with --browser --headful,
    slow down, or run from a residential connection."""


class Http:
    def __init__(self, min_delay: float = 2.0, max_delay: float = 5.0, retries: int = 4):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        })
        self.min_delay, self.max_delay, self.retries = min_delay, max_delay, retries
        self._last = 0.0

    def _pace(self) -> None:
        wait = random.uniform(self.min_delay, self.max_delay) - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def request(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", 30)
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self._pace()
            try:
                r = self.s.request(method, url, **kw)
            except requests.RequestException as e:
                last = e
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = SourceError(f"{r.status_code} from {url}")
                time.sleep(2 ** (attempt + 1) + random.random())
                continue
            head = r.text[:4000].lower() if "text" in r.headers.get("content-type", "") else ""
            if r.status_code in (403, 503) or any(m in head for m in BLOCK_MARKERS[:3]):
                raise Blocked(f"{r.status_code} bot wall at {url}")
            return r
        raise SourceError(f"giving up on {url}: {last}")

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
