"""Source registry. Each entry: collector(http, query, cfg) -> Iterator[Listing].

history = how far back sold data reaches for free. price = what the recorded price means.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import depop, ebay, etsy, grailed, mercari, poshmark, shopgoodwill, vestiaire


@dataclass(frozen=True)
class Source:
    name: str
    collect: Callable
    host: str
    history: str
    price: str
    needs: str = ""
    browser: bool = False


SOURCES: dict[str, Source] = {s.name: s for s in [
    Source("ebay_sold", ebay.collect_sold, "www.ebay.com", "~90 days", "sale price; list price when Best Offer accepted"),
    Source("ebay_active", ebay.collect_active, "api.ebay.com", "active only", "ask", "EBAY_CLIENT_ID/SECRET"),
    Source("grailed_sold", grailed.collect_sold, "mnrwefss2q-dsn.algolia.net", "years", "actual sale price"),
    Source("grailed_active", grailed.collect_active, "mnrwefss2q-dsn.algolia.net", "active only", "ask"),
    Source("poshmark_sold", poshmark.collect, "poshmark.com", "years", "last list price (offers hidden)"),
    Source("mercari_sold", mercari.collect, "www.mercari.com", "months-years", "sale price", browser=True),
    Source("depop", depop.collect, "webapi.depop.com", "thin", "ask / sale"),
    Source("etsy_active", etsy.collect, "openapi.etsy.com", "active only", "ask", "ETSY_API_KEY"),
    Source("vestiaire_sold", vestiaire.collect, "search.vestiairecollective.com", "undated", "sale price"),
    Source("shopgoodwill_closed", shopgoodwill.collect, "buyerapi.shopgoodwill.com", "~60 days", "hammer price"),
]}

# Not collectable for free: The RealReal (sold prices hidden), WorthPoint (paid, bans scraping),
# eBay Marketplace Insights (partner-only), Vinted (no US market, sold prices hidden).
