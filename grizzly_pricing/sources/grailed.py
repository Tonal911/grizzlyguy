"""Grailed via its Algolia search backend (what grailed.com's own search calls).

The sold index holds years of real transaction prices (sold_price), which makes this the
best free long-history source for menswear. Algolia caps any one query at 1,000 hits, so
collection walks the history in monthly sold-date windows.

The search key is public (shipped to every browser) and rotates. Resolution order:
GRAILED_ALGOLIA_KEY env var -> scraped from grailed.com HTML -> `probe discover` to read it
from the x-algolia-api-key request header.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Iterator
from urllib.parse import urlencode

from ..config import Config, env
from ..net import Http, SourceError
from ..schema import Listing, dig, to_float, to_iso_date

log = logging.getLogger(__name__)

APP_ID = env("GRAILED_ALGOLIA_APP") or "MNRWEFSS2Q"
SOLD_INDEX = env("GRAILED_SOLD_INDEX") or "Listing_sold_production"
ACTIVE_INDEX = env("GRAILED_ACTIVE_INDEX") or "Listing_production"
SOLD_TS_FIELD = env("GRAILED_SOLD_TS_FIELD") or "sold_at_i"

_key_cache: str | None = None


def api_key(http: Http) -> str:
    global _key_cache
    if _key_cache:
        return _key_cache
    k = env("GRAILED_ALGOLIA_KEY")
    if not k:
        html = http.get("https://www.grailed.com/").text
        m = re.search(r'["\'](?:api_?key|apiKey|search_?key)["\']\s*:\s*["\']([a-f0-9]{32})', html, re.I)
        pos = html.lower().find("algolia")
        if not m and pos >= 0:  # any 32-hex token near the Algolia config block
            m = re.search(r'["\']([a-f0-9]{32})["\']', html[pos:pos + 5000])
        if not m:
            raise SourceError("Grailed Algolia key not found; run `probe discover --url https://www.grailed.com/sold` "
                              "and set GRAILED_ALGOLIA_KEY from the x-algolia-api-key header")
        k = m.group(1)
    _key_cache = k
    return k


def _query(http: Http, index: str, params: dict) -> dict:
    r = http.post(
        f"https://{APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries",
        headers={"x-algolia-application-id": APP_ID, "x-algolia-api-key": api_key(http),
                 "Origin": "https://www.grailed.com", "Referer": "https://www.grailed.com/"},
        json={"requests": [{"indexName": index, "params": urlencode(params)}]},
    )
    if r.status_code in (401, 403):
        raise SourceError(f"Algolia rejected key ({r.status_code}); refresh GRAILED_ALGOLIA_KEY")
    r.raise_for_status()
    return r.json()["results"][0]


def hit_to_listing(h: dict, sold: bool, query: str | None) -> Listing:
    designers = h.get("designers") or []
    brand = h.get("designer_names") or ", ".join(d.get("name", "") for d in designers if isinstance(d, dict))
    price = dig(h, "sold_price", "price_i", "price") if sold else dig(h, "price_i", "price")
    return Listing(
        source="grailed",
        source_id=str(h.get("id") or h.get("objectID")),
        title=h.get("title", ""),
        price=to_float(price),
        url=f"https://www.grailed.com/listings/{h.get('id') or h.get('objectID')}",
        shipping=to_float(dig(h, "sold_shipping_price", "shipping.us.amount")),
        is_sold=sold,
        price_is_actual=sold,
        sold_at=to_iso_date(dig(h, "sold_at", "sold_at_i")) if sold else None,
        listed_at=to_iso_date(dig(h, "created_at", "created_at_i")),
        brand=brand or None,
        size=dig(h, "size", "size_label"),
        condition=dig(h, "condition"),
        description=h.get("description"),
        image_url=dig(h, "cover_photo.url", "cover_photo.image_url"),
        seller=dig(h, "user.username"),
        query=query,
        raw={k: v for k, v in h.items() if k not in ("_highlightResult", "photos")},
    )


def collect_sold(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    end = int(time.time())
    start = end - cfg.history_days * 86400
    base = {"query": query, "hitsPerPage": 100}
    total = _query(http, SOLD_INDEX, {**base, "hitsPerPage": 0}).get("nbHits", 0)
    log.info("grailed sold '%s': %d hits", query, total)
    windowed = 0
    t = start
    while t < end:
        t2 = min(t + 30 * 86400, end)
        flt = {**base, "numericFilters": f"{SOLD_TS_FIELD}>={t},{SOLD_TS_FIELD}<{t2}"}
        for page in range(10):  # 10 x 100 = Algolia's 1,000-hit ceiling
            res = _query(http, SOLD_INDEX, {**flt, "page": page})
            hits = res.get("hits", [])
            windowed += len(hits)
            for h in hits:
                yield hit_to_listing(h, True, query)
            if page + 1 >= res.get("nbPages", 0):
                break
        t = t2
    if total and windowed == 0:
        log.warning("date windows returned nothing; %s is probably not the sold-time field. "
                    "Falling back to unwindowed (max 1,000 most relevant).", SOLD_TS_FIELD)
        for page in range(10):
            res = _query(http, SOLD_INDEX, {**base, "page": page})
            for h in res.get("hits", []):
                yield hit_to_listing(h, True, query)
            if page + 1 >= res.get("nbPages", 0):
                break


def collect_active(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    for page in range(min(cfg.max_pages, 10)):
        res = _query(http, ACTIVE_INDEX, {"query": query, "hitsPerPage": 100, "page": page})
        for h in res.get("hits", []):
            yield hit_to_listing(h, False, query)
        if page + 1 >= res.get("nbPages", 0):
            break
