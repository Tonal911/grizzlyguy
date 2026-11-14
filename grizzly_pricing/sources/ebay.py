"""eBay, three free routes.

1. sold_html  - scrape completed/sold search results. ~90 days of history (eBay's limit).
2. browse_api - official Browse API, active listings only. Free developer key:
                developer.ebay.com -> Application Keys -> Production. 5,000 calls/day.
3. terapeak   - see terapeak.py. Free in Seller Hub, up to 3 years of sold data, manual export.

The Finding API's findCompletedItems (the old free sold-data route) was retired in Feb 2025.
Marketplace Insights API (sold data) is restricted to approved partners.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..config import Config, env
from ..net import Http, SourceError
from ..schema import Listing, dig, to_float, to_iso_date

log = logging.getLogger(__name__)

SOLD_URL = "https://www.ebay.com/sch/i.html"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

SOLD_RE = re.compile(r"Sold\s+([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})")
PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
SHIP_RE = re.compile(r"\+\s?\$([\d,]+\.\d{2})\s+(?:shipping|delivery)", re.I)
FREE_RE = re.compile(r"free (?:shipping|delivery)", re.I)
ITEM_RE = re.compile(r"/itm/(?:[^/?]+/)?(\d{9,15})")


def parse_sold_page(html: str, query: str | None = None) -> list[Listing]:
    """Parse eBay sold search results. Handles both the legacy `li.s-item` layout and the
    2025 `li.s-card` layout by keying off stable signals (item link, 'Sold <date>' text)."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    for li in soup.select("li.s-item, li.s-card, ul.srp-results > li"):
        a = li.select_one("a[href*='/itm/']")
        if not a:
            continue
        m = ITEM_RE.search(a.get("href", ""))
        if not m:
            continue
        title_el = li.select_one(".s-item__title, .s-card__title, [role=heading]")
        title = (title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True))
        title = re.sub(r"^New Listing\s*", "", title).replace("Opens in a new window or tab", "").strip()
        if not title or title.lower().startswith("shop on ebay"):
            continue
        text = li.get_text(" ", strip=True)
        price_el = li.select_one(".s-item__price, .s-card__price")
        price_txt = price_el.get_text(" ", strip=True) if price_el else text
        if " to " in price_txt:  # variation listing price ranges are not a sale price
            continue
        pm = PRICE_RE.search(price_txt)
        sold = SOLD_RE.search(text)
        ship = SHIP_RE.search(text)
        cond_el = li.select_one(".SECONDARY_INFO, .s-card__subtitle")
        img = li.select_one("img")
        out.append(Listing(
            source="ebay",
            source_id=m.group(1),
            title=title,
            price=to_float(pm.group(1)) if pm else None,
            url=f"https://www.ebay.com/itm/{m.group(1)}",
            shipping=to_float(ship.group(1)) if ship else (0.0 if FREE_RE.search(text) else None),
            is_sold=bool(sold),
            # Strikethrough price + "Best offer accepted" means the shown price is the ask.
            price_is_actual="best offer accepted" not in text.lower(),
            sold_at=to_iso_date(sold.group(1).replace(".", "")) if sold else None,
            condition=cond_el.get_text(" ", strip=True) if cond_el else None,
            image_url=(img.get("src") or img.get("data-src")) if img else None,
            query=query,
        ))
    return out


def collect_sold(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    for cat in cfg.ebay_category_ids or ["0"]:
        seen: set[str] = set()
        for page in range(1, cfg.max_pages + 1):
            r = http.get(SOLD_URL, params={
                "_nkw": query, "_sacat": cat, "LH_Sold": "1", "LH_Complete": "1",
                "_ipg": "240", "_pgn": str(page), "_sop": "13", "rt": "nc",
            })
            items = [i for i in parse_sold_page(r.text, query) if i.source_id not in seen]
            if not items:
                break
            seen.update(i.source_id for i in items)
            yield from items


def _token(http: Http) -> str:
    cid, secret = env("EBAY_CLIENT_ID"), env("EBAY_CLIENT_SECRET")
    if not (cid and secret):
        raise SourceError("set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (free at developer.ebay.com)")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = http.post(TOKEN_URL, headers={"Authorization": f"Basic {basic}"}, data={
        "grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"})
    r.raise_for_status()
    return r.json()["access_token"]


def browse_item(it: dict, query: str | None) -> Listing:
    return Listing(
        source="ebay_active",
        source_id=it["itemId"],
        title=it.get("title", ""),
        price=to_float(dig(it, "price.value", "currentBidPrice.value")),
        currency=dig(it, "price.currency", default="USD"),
        shipping=to_float(dig(it, "shippingOptions.0.shippingCost.value")),
        url=it.get("itemWebUrl"),
        is_sold=False,
        price_is_actual=False,
        listed_at=to_iso_date(it.get("itemCreationDate")),
        condition=it.get("condition"),
        image_url=dig(it, "image.imageUrl"),
        seller=dig(it, "seller.username"),
        query=query,
        raw=it,
    )


def collect_active(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    headers = {"Authorization": f"Bearer {_token(http)}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
    url, params = BROWSE_URL, {"q": query, "limit": "200",
                               "category_ids": ",".join(cfg.ebay_category_ids)}
    for _ in range(cfg.max_pages):
        r = http.get(url, headers=headers, params=params)
        r.raise_for_status()
        body = r.json()
        for it in body.get("itemSummaries", []):
            yield browse_item(it, query)
        url, params = body.get("next"), None
        if not url:
            break
