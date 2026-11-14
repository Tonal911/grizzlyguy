"""Depop. Tries the webapi search JSON first, then falls back to browser capture (Depop sits
behind Cloudflare). Depop's search mostly returns active items; sold items appear on seller
shop pages and sometimes in search with a sold status. Expect thin sold coverage here.
"""
from __future__ import annotations

import logging
from typing import Iterator
from urllib.parse import quote_plus

from ..browser import capture_json
from ..config import Config
from ..net import Blocked, Http
from ..schema import Listing, dig, to_float, to_iso_date

log = logging.getLogger(__name__)

API = "https://webapi.depop.com/api/v3/search/products/"


def product_to_listing(p: dict, query: str | None) -> Listing:
    status = str(dig(p, "status", "sold", default="")).lower()
    sold = status in ("sold", "true", "s")
    slug = p.get("slug") or p.get("id")
    return Listing(
        source="depop",
        source_id=str(p.get("id") or slug),
        title=dig(p, "description", "title", default="")[:300],
        price=to_float(dig(p, "pricing.final_price_key", "price.priceAmount", "price.amount",
                           "pricing.original_price.price_breakdown.price.amount", "price")),
        currency=dig(p, "price.currencyName", "pricing.currency_name", default="USD"),
        url=f"https://www.depop.com/products/{slug}/",
        is_sold=sold,
        price_is_actual=sold,
        listed_at=to_iso_date(dig(p, "dateCreated", "date_created")),
        brand=dig(p, "brandName", "brand_name", "brand.name"),
        size=dig(p, "sizes.0", "size"),
        condition=dig(p, "condition"),
        description=p.get("description"),
        image_url=dig(p, "preview.320", "pictures.0.0.url"),
        seller=dig(p, "seller.username", "sellerUsername"),
        query=query,
        raw=p,
    )


def _products(body: dict) -> list[dict]:
    return dig(body, "products", "objects", "data", default=[]) or []


def collect(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    try:
        cursor = None
        for _ in range(cfg.max_pages):
            params = {"what": query, "items_per_page": "24", "country": "us", "currency": "USD"}
            if cursor:
                params["cursor"] = cursor
            r = http.get(API, params=params, headers={"Accept": "application/json",
                                                      "Origin": "https://www.depop.com"})
            r.raise_for_status()
            body = r.json()
            for p in _products(body):
                yield product_to_listing(p, query)
            cursor = dig(body, "meta.cursor", "meta.next_cursor")
            if not cursor or not dig(body, "meta.has_more", default=True):
                break
        return
    except (Blocked, ValueError) as e:
        log.warning("depop API blocked (%s); using browser capture", e)
    for _, body in capture_json(f"https://www.depop.com/search/?q={quote_plus(query)}",
                                lambda u: "webapi.depop.com" in u and "search" in u,
                                scrolls=cfg.max_pages, headless=cfg.headless):
        for p in _products(body):
            yield product_to_listing(p, query)
