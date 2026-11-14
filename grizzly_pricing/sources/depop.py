"""Depop. Tries the webapi search JSON first. It is bot-walled from datacenter IPs (verified
2026-11-14), so the fallback fetches the search page through ZenRows (25 credits per page,
24 listings) and parses the product cards. Search shows active listings: asking prices
only, used for comparison, not in the sold-price model. Set GRIZZLY_DEPOP_VIA=browser to use
local Playwright capture instead of ZenRows.
"""
from __future__ import annotations

import logging
from typing import Iterator
from urllib.parse import quote_plus

import re

from bs4 import BeautifulSoup

from ..browser import capture_json
from ..config import Config, env
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
        log.warning("depop API blocked (%s); falling back", e)
    if env("GRIZZLY_DEPOP_VIA") == "browser":
        for _, body in capture_json(f"https://www.depop.com/search/?q={quote_plus(query)}",
                                    lambda u: "webapi.depop.com" in u and "search" in u,
                                    scrolls=cfg.max_pages, headless=cfg.headless):
            for p in _products(body):
                yield product_to_listing(p, query)
        return
    from .. import zenrows

    url = f"https://www.depop.com/search/?q={quote_plus(query)}"
    yield from parse_search_html(zenrows.fetch([url])[url] or "", query)


PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")


def parse_search_html(html: str, query: str | None = None) -> list[Listing]:
    """Parse depop.com/search product cards (markup verified 2026-11-14): each card has an
    <a href="/products/<slug>/" aria-label="<brand + description>"> and a
    <p aria-description="Price">$57.50</p>. Class names are hashed, so they are not used."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select('a[href^="/products/"]'):
        slug = a["href"].strip("/").split("/")[-1]
        if slug in seen:
            continue
        card = a
        while card is not None and card.name != "li":
            card = card.parent
        if card is None:
            continue
        price_el = card.select_one('[aria-description="Price"]') or card.find(string=PRICE_RE)
        txt = price_el.get_text(" ", strip=True) if hasattr(price_el, "get_text") else str(price_el or "")
        m = PRICE_RE.search(txt)
        if not m:
            continue
        seen.add(slug)
        size_el = card.select_one('[class*="sizeAttributeText"]')
        img = card.select_one("img")
        title = a.get("aria-label") or (img.get("alt") if img else "") or slug.replace("-", " ")
        out.append(Listing(
            source="depop", source_id=slug, title=title, price=to_float(m.group(1)),
            url=f"https://www.depop.com/products/{slug}/", is_sold=False, price_is_actual=False,
            size=size_el.get_text(strip=True) if size_el else None,
            # Slug text often carries the seller's own description ("black-faux-fur-lined-leather").
            # Slug = "<seller>-<seller's description>-<4-char id>".
            description=slug.split("-", 1)[-1].rsplit("-", 1)[0].replace("-", " "),
            seller=slug.split("-", 1)[0].rstrip("_") or None,
            image_url=img.get("src") if img else None, query=query,
        ))
    return out
