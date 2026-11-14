"""Etsy Open API v3 (official, free key at etsy.com/developers). Active listings only; Etsy
does not expose sold prices. Its value: `when_made` (e.g. "1980s") is a seller-declared era
field, which is the best structured age signal of any source. Used for asking-price
comparison and for training the age extractor.

Auth: x-api-key header. Etsy has changed the required format (keystring vs
"keystring:shared_secret"); set ETSY_API_KEY to whatever the developer portal says.
"""
from __future__ import annotations

from typing import Iterator

from ..config import Config, env
from ..net import Http, SourceError
from ..schema import Listing, dig, to_iso_date

URL = "https://openapi.etsy.com/v3/application/listings/active"


def item_to_listing(it: dict, query: str | None) -> Listing:
    amt, div = dig(it, "price.amount"), dig(it, "price.divisor", default=100)
    return Listing(
        source="etsy",
        source_id=str(it["listing_id"]),
        title=it.get("title", ""),
        price=round(amt / div, 2) if amt is not None else None,
        currency=dig(it, "price.currency_code", default="USD"),
        url=it.get("url"),
        is_sold=False,
        price_is_actual=False,
        listed_at=to_iso_date(it.get("original_creation_timestamp") or it.get("creation_timestamp")),
        description=it.get("description"),
        age_hint=it.get("when_made"),
        query=query,
        raw={k: it.get(k) for k in ("when_made", "tags", "materials", "taxonomy_id", "quantity")},
    )


def collect(http: Http, query: str, cfg: Config) -> Iterator[Listing]:
    key = env("ETSY_API_KEY")
    if not key:
        raise SourceError("set ETSY_API_KEY (free at etsy.com/developers)")
    for page in range(cfg.max_pages):
        r = http.get(URL, headers={"x-api-key": key},
                     params={"keywords": query, "limit": 100, "offset": page * 100})
        r.raise_for_status()
        results = r.json().get("results", [])
        for it in results:
            yield item_to_listing(it, query)
        if len(results) < 100:
            break
