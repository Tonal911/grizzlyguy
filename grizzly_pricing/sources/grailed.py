"""Grailed via its Algolia search backend (what grailed.com's own search calls).

The sold index holds years of real transaction prices (sold_price), which makes this the
best free long-history source for menswear. Algolia caps any one query at 1,000 hits, so
collection walks the history in monthly sold-date windows.

The search key is public (shipped to every browser) and rotates; see api_key() for how it
is found. Verified live 2026-11-14: sold hits carry sold_price, sold_at, sold_at_i,
designers, size, condition, color; "leather jacket fur collar" alone has 121 sold hits,
36 of them older than 700 days.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Iterator
from urllib.parse import urlencode

from ..config import DATA_DIR, Config, env
from ..net import Blocked, Http, SourceError
from ..schema import Listing, dig, to_float, to_iso_date

log = logging.getLogger(__name__)

APP_ID = env("GRAILED_ALGOLIA_APP") or "MNRWEFSS2Q"
SOLD_INDEX = env("GRAILED_SOLD_INDEX") or "Listing_sold_production"
ACTIVE_INDEX = env("GRAILED_ACTIVE_INDEX") or "Listing_production"
SOLD_TS_FIELD = env("GRAILED_SOLD_TS_FIELD") or "sold_at_i"

_key_cache: str | None = None


KEY_CACHE_FILE = DATA_DIR / "grailed_key.json"


def key_from_html(html: str) -> str | None:
    """grailed.com ships its public Algolia config in __NEXT_DATA__ (verified 2026-11-14):
    props.initialProps.globalData.public_config.algolia.publicSearchKey."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            cfg = json.loads(m.group(1))["props"]["initialProps"]["globalData"]["public_config"]["algolia"]
            if cfg.get("publicSearchKey"):
                return cfg["publicSearchKey"]
        except (ValueError, KeyError, TypeError):
            pass
    m = re.search(r'"publicSearchKey"\s*:\s*"([a-f0-9]{32})"', html)
    return m.group(1) if m else None


def api_key(http: Http, refresh: bool = False) -> str:
    """Resolution: GRAILED_ALGOLIA_KEY -> cached key -> grailed.com direct -> grailed.com via
    ZenRows (25 credits; grailed.com is bot-walled from datacenter IPs)."""
    global _key_cache
    if _key_cache and not refresh:
        return _key_cache
    k = env("GRAILED_ALGOLIA_KEY")
    if not k and not refresh and KEY_CACHE_FILE.exists():
        k = json.loads(KEY_CACHE_FILE.read_text()).get("key")
    if not k:
        try:
            k = key_from_html(http.get("https://www.grailed.com/").text)
        except Blocked:
            log.info("grailed.com bot-walled; fetching homepage via ZenRows")
            from .. import zenrows

            k = key_from_html(zenrows.fetch(["https://www.grailed.com/"])["https://www.grailed.com/"] or "")
        if not k:
            raise SourceError("Grailed Algolia key not found; set GRAILED_ALGOLIA_KEY from the "
                              "x-algolia-api-key header (`probe discover --url https://www.grailed.com/sold`)")
        KEY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_CACHE_FILE.write_text(json.dumps({"key": k}))  # public key, shipped to every browser
    _key_cache = k
    return k


def _query(http: Http, index: str, params: dict, _retry: bool = True) -> dict:
    try:
        r = http.post(
            f"https://{APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries",
            headers={"x-algolia-application-id": APP_ID, "x-algolia-api-key": api_key(http),
                     "Origin": "https://www.grailed.com", "Referer": "https://www.grailed.com/"},
            json={"requests": [{"indexName": index, "params": urlencode(params)}]},
        )
    except Blocked:
        # Algolia answers a stale/rotated key with 403, which Http reports as Blocked.
        if not _retry or env("GRAILED_ALGOLIA_KEY"):
            raise SourceError("Algolia rejected the Grailed key; refresh GRAILED_ALGOLIA_KEY")
        log.info("Grailed key rejected; re-reading it from grailed.com")
        api_key(http, refresh=True)
        return _query(http, index, params, _retry=False)
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
        # sold_price_includes_shipping: shipping is already inside price, don't add it twice.
        shipping=0.0 if sold and h.get("sold_price_includes_shipping")
        else to_float(dig(h, "sold_shipping_price", "shipping.us.amount")),
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


# Grailed's category facet for leather jackets (seen on live sold hits). Searching short
# style terms inside it finds fur/shearling-collar jackets whose titles don't use the exact
# phrases in DEFAULT_QUERIES.
LEATHER_CATEGORIES = ["category_path:outerwear.leather_jackets", "category_path:womens_outerwear.leather_jackets"]
CATEGORY_TERMS = ["fur", "shearling", "sherpa", "raccoon", "mouton", "fox", "coyote", "grizzly", "fur collar"]


def collect_sold_category(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    """Sold leather jackets matching each CATEGORY_TERMS term (the passed query is ignored;
    the source runs its own term list once). Walks monthly sold-date windows."""
    if query != cfg.queries[0]:
        return
    end = int(time.time())
    for term in CATEGORY_TERMS:
        t = end - cfg.history_days * 86400
        while t < end:
            t2 = min(t + 30 * 86400, end)
            params = {"query": term, "hitsPerPage": 100, "facetFilters": json.dumps([LEATHER_CATEGORIES]),
                      "numericFilters": f"{SOLD_TS_FIELD}>={t},{SOLD_TS_FIELD}<{t2}"}
            for page in range(10):
                res = _query(http, SOLD_INDEX, {**params, "page": page})
                for h in res.get("hits", []):
                    yield hit_to_listing(h, True, f"category:{term}")
                if page + 1 >= res.get("nbPages", 0):
                    break
            t = t2


def collect_active(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    for page in range(min(cfg.max_pages, 10)):
        res = _query(http, ACTIVE_INDEX, {"query": query, "hitsPerPage": 100, "page": page})
        for h in res.get("hits", []):
            yield hit_to_listing(h, False, query)
        if page + 1 >= res.get("nbPages", 0):
            break
