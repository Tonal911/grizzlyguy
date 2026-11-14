"""Headless-browser capture of a site's own JSON API calls.

Used where plain HTTP clients get bot-walled or the API needs per-session tokens
(Mercari's GraphQL, Depop). The page runs normally; every JSON response whose URL
matches a predicate is kept. This also survives endpoint/hash changes, because we
read whatever the site's frontend actually calls.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable

log = logging.getLogger(__name__)


def capture_json(
    url: str,
    match: Callable[[str], bool],
    scrolls: int = 8,
    headless: bool = True,
    wait_ms: int = 2500,
) -> list[tuple[str, dict]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install playwright && playwright install chromium") from e

    captured: list[tuple[str, dict]] = []

    def on_response(resp):
        try:
            if match(resp.url) and "json" in resp.headers.get("content-type", ""):
                captured.append((resp.url, resp.json()))
        except Exception:  # body unavailable (redirect, aborted)
            pass

    launch = {"headless": headless}
    if os.environ.get("GRIZZLY_CHROMIUM"):
        launch["executable_path"] = os.environ["GRIZZLY_CHROMIUM"]
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(locale="en-US", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(wait_ms)
        if not headless:
            log.warning("Headful mode: solve any captcha in the window; capture continues.")
        for _ in range(scrolls):
            page.mouse.wheel(0, 6000)
            page.wait_for_timeout(wait_ms)
        browser.close()
    log.info("captured %d JSON responses from %s", len(captured), url)
    return captured


def discover(url: str, out_dir, scrolls: int = 4, headless: bool = True) -> list[dict]:
    """Log every JSON endpoint a page calls, with top-level keys, and save bodies.
    Run this when a source breaks to find its current internal API."""
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    hits = capture_json(url, lambda u: True, scrolls=scrolls, headless=headless)
    summary = []
    for i, (u, body) in enumerate(hits):
        keys = list(body.keys())[:15] if isinstance(body, dict) else f"list[{len(body)}]"
        summary.append({"i": i, "url": u[:300], "keys": keys, "bytes": len(json.dumps(body))})
        (out / f"{i:03d}.json").write_text(json.dumps({"url": u, "body": body}, indent=1)[:2_000_000])
    (out / "index.json").write_text(json.dumps(summary, indent=1))
    return summary
