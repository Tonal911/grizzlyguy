"""Fetch bot-protected pages through the ZenRows Batch API (residential proxies + JS rendering).

Measured from a cloud IP (2026-11-14), premium_proxy + js_render, 25 credits per success,
failures free:
  grailed.com  works   (only the homepage is needed, to read the public search key)
  depop.com    works   (search page HTML, 24 active listings per page)
  ebay.com     fails   (RESP001 on 3 of 3 attempts, auto mode and premium mode)
  mercari.com  returns an empty app shell (no listings)

Auth: the key goes in the X-API-Key header. In a Claude cloud session the network proxy
injects it, so the code sends a placeholder. Elsewhere, set ZENROWS_API_KEY.

Credit spend is recorded in data/zenrows_spend.json. Jobs are refused once the total would
pass GRIZZLY_ZENROWS_BUDGET (default 4500, leaving headroom under the 5k free tier).
"""
from __future__ import annotations

import json
import logging
import time

import requests

from .config import DATA_DIR, env
from .net import SourceError

log = logging.getLogger(__name__)

API = "https://async.api.zenrows.com/v1/jobs"
CREDITS_PER_TASK = 25  # premium_proxy + js_render
DEFAULT_PARAMS = {"js_render": True, "premium_proxy": True, "proxy_country": "us", "wait": 4000}
SPEND_FILE = DATA_DIR / "zenrows_spend.json"


def _headers() -> dict:
    return {"X-API-Key": env("ZENROWS_API_KEY") or "proxy-injected", "Content-Type": "application/json"}


def spent() -> int:
    try:
        return int(json.loads(SPEND_FILE.read_text())["credits"])
    except (FileNotFoundError, KeyError, ValueError):
        return 0


def _record(credits: int) -> None:
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPEND_FILE.write_text(json.dumps({"credits": spent() + credits}))


def budget() -> int:
    return int(env("GRIZZLY_ZENROWS_BUDGET") or 4500)


def fetch(urls: list[str], params: dict | None = None, timeout_s: int = 600) -> dict[str, str | None]:
    """Fetch URLs in one batch job. Returns {url: html or None if ZenRows failed on it}."""
    if not urls:
        return {}
    worst = len(urls) * CREDITS_PER_TASK
    if spent() + worst > budget():
        raise SourceError(f"ZenRows budget: {spent()} spent + up to {worst} > {budget()} "
                          "(raise GRIZZLY_ZENROWS_BUDGET if the plan allows)")
    s = requests.Session()
    s.headers.update(_headers())
    body = {"type": "regular", "status": "closed", "zenrows_params": params or DEFAULT_PARAMS,
            "tasks": [{"url": u, "external_id": str(i)} for i, u in enumerate(urls)]}
    r = s.post(API, json=body, timeout=60)
    if r.status_code in (401, 403):
        raise SourceError(f"ZenRows rejected the key ({r.status_code})")
    r.raise_for_status()
    job = r.json()["job_id"]
    deadline = time.time() + timeout_s
    while True:
        st = s.get(f"{API}/{job}", timeout=30).json()["latest_run"]
        if st["status"] not in ("pending", "running"):
            break
        if time.time() > deadline:
            raise SourceError(f"ZenRows job {job} still {st['status']} after {timeout_s}s")
        time.sleep(10)
    _record(int(st["stats"].get("spend", {}).get("credits", 0)))
    out: dict[str, str | None] = {u: None for u in urls}
    cursor = None
    while True:
        q = {"status": "successful", "limit": 100}
        if cursor:
            q["cursor"] = cursor
        page = s.get(f"{API}/{job}/results", params=q, timeout=30).json()
        for res in page.get("results", []):
            html = requests.get(res["result_url"], timeout=60)  # presigned S3 link, no auth
            if html.ok:
                out[urls[int(res["external_id"])]] = html.text
        cursor = page.get("next_cursor")
        if not cursor:
            break
    log.info("zenrows job %s: %d/%d ok, %s credits (total %d)", job,
             sum(v is not None for v in out.values()), len(urls), st["stats"].get("spend"), spent())
    return out
