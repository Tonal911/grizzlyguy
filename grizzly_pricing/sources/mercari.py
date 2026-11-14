"""Mercari US. The search API is GraphQL with persisted-query hashes and DPoP-signed
requests, so a plain HTTP client can't reach it. Collection drives a real browser on the
sold-items search page and keeps the JSON the page receives.

itemStatuses=2 selects sold items. Field names are matched loosely, since the GraphQL schema is
undocumented. Run `probe sample --source mercari` first and check the field coverage it prints.
"""
from __future__ import annotations

from typing import Iterator
from urllib.parse import quote_plus

from ..browser import capture_json
from ..config import Config
from ..net import Http
from ..schema import Listing, dig, to_float, to_iso_date


def _walk_items(node) -> Iterator[dict]:
    """Yield dicts that look like Mercari items wherever they sit in the GraphQL response."""
    if isinstance(node, dict):
        if "id" in node and "name" in node and ("price" in node or "status" in node):
            yield node
            return
        for v in node.values():
            yield from _walk_items(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_items(v)


def _price(v) -> float | None:
    # Mercari US GraphQL reports prices in cents (integer). Verify with `probe sample`.
    f = to_float(v)
    return round(f / 100, 2) if f is not None and isinstance(v, int) else f


def item_to_listing(it: dict, query: str | None) -> Listing:
    status = str(it.get("status", "")).lower()
    sold = "sold" in status or status in ("trading", "2")
    return Listing(
        source="mercari",
        source_id=str(it["id"]),
        title=it.get("name", ""),
        price=_price(it.get("price")),
        url=f"https://www.mercari.com/us/item/{it['id']}/",
        is_sold=sold,
        price_is_actual=sold,
        sold_at=to_iso_date(dig(it, "updated", "updatedAt", "soldAt")) if sold else None,
        listed_at=to_iso_date(dig(it, "created", "createdAt")),
        brand=dig(it, "brand.name", "brandName"),
        size=dig(it, "itemSize.name", "size.name"),
        condition=dig(it, "itemCondition.name", "condition.name"),
        description=it.get("description"),
        image_url=dig(it, "photos.0.thumbnail", "photos.0.imageUrl", "thumbnail"),
        seller=str(dig(it, "seller.id", default="")) or None,
        query=query,
        raw=it,
    )


def collect(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    url = f"https://www.mercari.com/search/?keyword={quote_plus(query)}&itemStatuses=2"
    seen = set()
    for _, body in capture_json(url, lambda u: "/v1/api" in u or "graphql" in u,
                                scrolls=cfg.max_pages, headless=cfg.headless):
        for it in _walk_items(body):
            if it["id"] not in seen:
                seen.add(it["id"])
                yield item_to_listing(it, query)
