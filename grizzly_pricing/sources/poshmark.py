"""Poshmark via the /vm-rest/posts JSON endpoint behind poshmark.com search.

Sold listings ("sold_out") go back years. Caveat: the price shown is the last list price.
Accepted offers are private, so Poshmark sold prices run high relative to real transactions.
They are flagged price_is_actual=False, and the model gives each source its own effect.
"""
from __future__ import annotations

import json
from typing import Iterator

from ..browser import capture_json
from ..config import Config
from ..net import Http
from ..schema import Listing, dig, to_float, to_iso_date

URL = "https://poshmark.com/vm-rest/posts"


def item_to_listing(p: dict, query: str | None) -> Listing:
    status = str(dig(p, "inventory.status", "status", default="")).lower()
    sold = status in ("sold_out", "sold")
    return Listing(
        source="poshmark",
        source_id=str(p.get("id")),
        title=p.get("title", ""),
        price=to_float(dig(p, "price_amount.val", "price")),
        currency=dig(p, "price_amount.currency_code", default="USD"),
        url=f"https://poshmark.com/listing/{p.get('id')}",
        is_sold=sold,
        price_is_actual=False,
        sold_at=to_iso_date(dig(p, "inventory.status_changed_at")) if sold else None,
        listed_at=to_iso_date(dig(p, "first_published_at", "created_at")),
        brand=dig(p, "brand", "brand_obj.canonical_name"),
        size=dig(p, "size_obj.display", "size"),
        condition=dig(p, "condition"),
        description=p.get("description"),
        image_url=dig(p, "picture_url", "cover_shot.url"),
        seller=dig(p, "creator_username"),
        query=query,
        raw={k: p.get(k) for k in ("original_price_amount", "department", "category", "colors",
                                    "inventory", "created_at", "first_published_at")},
    )


def _request(query: str, sold: bool, max_id) -> dict:
    req = {"filters": {"department": "All", "inventory_status": ["sold_out" if sold else "available"]},
           "query": query, "experience": "all", "count": "48"}
    if max_id:
        req["max_id"] = max_id
    return {"request": json.dumps(req, separators=(",", ":")), "summarize": "true"}


def collect(http: Http, query: str, cfg: Config, sold: bool = True) -> Iterator[Listing]:
    max_id = None
    for _ in range(cfg.max_pages):
        r = http.get(URL, params=_request(query, sold, max_id),
                     headers={"Accept": "application/json", "Referer": "https://poshmark.com/"})
        r.raise_for_status()
        body = r.json()
        for p in body.get("data", []):
            yield item_to_listing(p, query)
        max_id = dig(body, "more.next_max_id")
        if not max_id or not body.get("data"):
            break


def collect_browser(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    from urllib.parse import quote_plus

    hits = capture_json(f"https://poshmark.com/search?query={quote_plus(query)}&availability=sold_out",
                        lambda u: "vm-rest/posts" in u, scrolls=cfg.max_pages, headless=cfg.headless)
    for _, body in hits:
        for p in body.get("data", []):
            yield item_to_listing(p, query)
