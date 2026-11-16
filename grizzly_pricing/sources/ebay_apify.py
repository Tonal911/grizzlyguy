"""eBay sold listings via the Apify actor caffein.dev/ebay-sold-listings.

eBay's sold search is bot-walled from datacenter IPs and ZenRows could not get through
(2026-11-14). This actor can (verified 2026-11-16). Reach: eBay's ~90-day sold window, so
run it on a schedule to build history; use the Terapeak import for older eBay sales.

Cost (free tier, verified 2026-11-16): $0.004 per result + a small per-run start fee. The free
plan's $5/month buys ~1,200 results. Every run is capped with maxTotalChargeUsd, and the
running total is kept in data/apify_spend.json against GRIZZLY_APIFY_BUDGET_USD (default 4.50).

Auth: Bearer token. In a Claude cloud session the network proxy injects it; elsewhere set
APIFY_TOKEN.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterator

import requests

from ..config import DATA_DIR, Config, env
from ..net import SourceError
from ..schema import Listing, to_float, to_iso_date

log = logging.getLogger(__name__)

ACTOR = "caffein.dev~ebay-sold-listings"
API = "https://api.apify.com/v2"
PRICE_PER_RESULT = 0.004
SPEND_FILE = DATA_DIR / "apify_spend.json"
# Men's / women's Coats, Jackets & Vests.
CATEGORIES = {"57988": "men", "63862": "women"}


def spent() -> float:
    try:
        return float(json.loads(SPEND_FILE.read_text())["usd"])
    except (FileNotFoundError, KeyError, ValueError):
        return 0.0


def _record(usd: float) -> None:
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPEND_FILE.write_text(json.dumps({"usd": round(spent() + usd, 4)}))


def item_to_listing(x: dict) -> Listing:
    best_offer = bool(x.get("isBestOfferAccepted"))
    return Listing(
        source="ebay",
        source_id=str(x["itemId"]),
        title=x.get("title") or "",
        price=to_float(x.get("soldPrice")),
        currency=x.get("soldCurrency") or "USD",
        shipping=to_float(x.get("shippingPrice")),
        url=x.get("url"),
        is_sold=True,
        # On Best Offer sales eBay shows the list price, not the accepted offer.
        price_is_actual=not best_offer,
        sold_at=to_iso_date(x.get("endedAt")),
        condition=x.get("condition"),
        image_url=x.get("fullResThumbnailUrl") or x.get("thumbnailUrl"),
        seller=x.get("sellerUsername"),
        query=x.get("keyword"),
        raw={k: x.get(k) for k in ("isBestOfferAccepted", "buyingFormat", "bidCount", "category",
                                    "categoryId", "conditionId", "soldPriceMax", "totalPrice", "itemLocation")},
    )


def collect(http, query: str, cfg: Config) -> Iterator[Listing]:
    """One actor run per category covering all cfg.queries (the actor takes a keyword list),
    so this does its work on the first query and skips the rest."""
    if query != cfg.queries[0]:
        return
    per_kw = int(env("GRIZZLY_APIFY_COUNT") or 50)
    budget = float(env("GRIZZLY_APIFY_BUDGET_USD") or 4.50)
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {env('APIFY_TOKEN') or 'proxy-injected'}"
    # The actor accepts at most 6 keywords per run (input validation, 2026-11-16).
    chunks = [cfg.queries[i:i + 6] for i in range(0, len(cfg.queries), 6)]
    for cat, kws in ((c, k) for c in CATEGORIES for k in chunks):
        cap = round(per_kw * len(kws) * PRICE_PER_RESULT + 0.05, 2)
        if spent() + cap > budget:
            raise SourceError(f"Apify budget: ${spent():.2f} spent + up to ${cap} > ${budget} "
                              "(raise GRIZZLY_APIFY_BUDGET_USD if your plan allows)")
        body = {"keywords": kws, "categoryId": cat, "daysToScrape": 90,
                "count": per_kw, "ebaySite": "ebay.com"}
        # Async run: start, poll, read dataset (run-sync endpoints cap out at 300 s).
        r = s.post(f"{API}/acts/{ACTOR}/runs", params={"maxTotalChargeUsd": cap}, json=body, timeout=60)
        if r.status_code in (401, 403):
            raise SourceError(f"Apify rejected the token ({r.status_code})")
        if not r.ok:
            raise SourceError(f"Apify {r.status_code}: {r.text[:300]}")
        run = r.json()["data"]
        deadline = time.time() + 1800
        while run["status"] in ("READY", "RUNNING"):
            if time.time() > deadline:
                raise SourceError(f"Apify run {run['id']} still {run['status']} after 30 min")
            time.sleep(15)
            run = s.get(f"{API}/actor-runs/{run['id']}", timeout=30).json()["data"]
        if run["status"] != "SUCCEEDED":
            log.warning("apify run %s ended %s; keeping any partial results", run["id"], run["status"])
        items = s.get(f"{API}/datasets/{run['defaultDatasetId']}/items", params={"clean": "true"}, timeout=120).json()
        # usageTotalUsd already includes the per-result charges (checked against the account's
        # monthly usage, 2026-11-16); estimate only if Apify didn't report it.
        usd = run.get("usageTotalUsd") or len(items) * PRICE_PER_RESULT
        _record(usd)
        log.info("apify ebay cat %s: %d sold items (est. total spend $%.2f)", cat, len(items), spent())
        for x in items:
            if x.get("itemId"):
                yield item_to_listing(x)
