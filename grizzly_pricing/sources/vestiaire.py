"""Vestiaire Collective search API (behind the site's catalog pages). Sold items are
searchable with filters.sold=["1"]. Sold dates are not exposed in search results as far as
known; those rows get sold_at=None and are used for level effects, not for the time index.
"""
from __future__ import annotations

from typing import Iterator

from ..browser import capture_json
from ..config import Config
from ..net import Blocked, Http
from ..schema import Listing, dig, to_float, to_iso_date

URL = "https://search.vestiairecollective.com/v1/product/search"
FIELDS = ["name", "description", "brand", "model", "country", "price", "discount", "link",
          "sold", "likes", "seller", "pictures", "colors", "size", "condition", "material",
          "universeId", "createdAt"]


def item_to_listing(it: dict, query: str | None) -> Listing:
    cents = dig(it, "price.cents")
    sold = bool(it.get("sold"))
    link = it.get("link", "")
    return Listing(
        source="vestiaire",
        source_id=str(it.get("id")),
        title=it.get("name", ""),
        price=round(cents / 100, 2) if cents is not None else to_float(dig(it, "price.amount")),
        currency=dig(it, "price.currency", default="USD"),
        url=f"https://www.vestiairecollective.com{link}" if link.startswith("/") else link or None,
        is_sold=sold,
        price_is_actual=sold,
        sold_at=to_iso_date(dig(it, "soldAt", "sold_at")) if sold else None,
        listed_at=to_iso_date(dig(it, "createdAt", "created_at")),
        brand=dig(it, "brand.name"),
        size=dig(it, "size.size", "size.name"),
        condition=dig(it, "condition.description", "condition.name"),
        description=it.get("description"),
        image_url=dig(it, "pictures.0"),
        seller=dig(it, "seller.firstname"),
        query=query,
        raw={k: it.get(k) for k in ("material", "colors", "country", "model", "discount")},
    )


def collect(http: Http, query: str, cfg: Config, sold: bool = True) -> Iterator[Listing]:
    try:
        for page in range(cfg.max_pages):
            body = {
                "pagination": {"offset": page * 48, "limit": 48},
                "fields": FIELDS, "facets": {"fields": [], "stats": []},
                "q": query, "sortBy": "relevance",
                "filters": {"sold": ["1" if sold else "0"]},
                "locale": {"country": "US", "currency": "USD", "language": "en", "sizeType": "US"},
            }
            r = http.post(URL, json=body, headers={"Origin": "https://www.vestiairecollective.com"})
            r.raise_for_status()
            items = r.json().get("items", [])
            for it in items:
                yield item_to_listing(it, query)
            if len(items) < 48:
                break
        return
    except Blocked:
        pass
    from urllib.parse import quote_plus

    for _, b in capture_json(f"https://www.vestiairecollective.com/search/?q={quote_plus(query)}",
                             lambda u: "product/search" in u, scrolls=cfg.max_pages, headless=cfg.headless):
        for it in b.get("items", []):
            yield item_to_listing(it, query)
