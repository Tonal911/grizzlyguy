"""ShopGoodwill buyer API (the JSON API shopgoodwill.com's frontend calls). Closed auctions
give real hammer prices. History depth is limited by closedAuctionDaysBack (the site's UI
offers up to ~30-60 days), so run it on a schedule to accumulate.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from ..config import Config
from ..net import Http
from ..schema import Listing, to_float, to_iso_date

URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"


def body(query: str, page: int, closed: bool, days_back: int) -> dict:
    return {
        "isSize": False, "isWeddingCatagory": "false", "isMultipleCategoryIds": False,
        "isFromHeaderMenuTab": False, "layout": "", "isFromHomePage": False,
        "searchText": query, "selectedGroup": "", "selectedCategoryIds": "",
        "selectedSellerIds": "", "lowPrice": "0", "highPrice": "999999",
        "searchBuyNowOnly": "", "searchPickupOnly": "false", "searchNoPickupOnly": "false",
        "searchOneCentShippingOnly": "false", "searchDescriptions": "false",
        "searchClosedAuctions": "true" if closed else "false",
        "closedAuctionEndingDate": dt.date.today().strftime("%-m/%-d/%Y") if closed else "",
        "closedAuctionDaysBack": str(days_back) if closed else "",
        "searchCanadaShipping": "false", "searchInternationalShippingOnly": "false",
        "sortColumn": "1", "page": str(page), "pageSize": "40", "sortDescending": "false",
        "savedSearchId": 0, "useBuyerPrefs": "true", "searchUSOnlyShipping": "true",
        "categoryLevelNo": "1", "categoryLevel": 1, "categoryId": 0, "partNumber": "", "catIds": "",
    }


def item_to_listing(it: dict, closed: bool, query: str | None) -> Listing:
    bids = it.get("numBids") or 0
    sold = closed and bids > 0
    return Listing(
        source="shopgoodwill",
        source_id=str(it.get("itemId")),
        title=it.get("title", ""),
        price=to_float(it.get("currentPrice")),
        url=f"https://shopgoodwill.com/item/{it.get('itemId')}",
        is_sold=sold,
        price_is_actual=sold,
        sold_at=to_iso_date((it.get("endTime") or "")[:10]) if sold else None,
        listed_at=to_iso_date((it.get("startTime") or "")[:10]),
        image_url=it.get("imageURL"),
        seller=it.get("sellerName"),
        query=query,
        raw={k: it.get(k) for k in ("numBids", "endTime", "categoryName", "sellerName")},
    )


def collect(http: Http, query: str, cfg: Config, closed: bool = True) -> Iterator[Listing]:
    for page in range(1, cfg.max_pages + 1):
        r = http.post(URL, json=body(query, page, closed, 60),
                      headers={"Origin": "https://shopgoodwill.com", "Referer": "https://shopgoodwill.com/"})
        r.raise_for_status()
        items = (r.json().get("searchResults") or {}).get("items") or []
        for it in items:
            yield item_to_listing(it, closed, query)
        if len(items) < 40:
            break
