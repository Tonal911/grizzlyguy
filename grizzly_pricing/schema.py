from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def to_iso_date(value: Any) -> str | None:
    """Normalize epoch seconds/ms, ISO strings, or 'Oct 3, 2026' into YYYY-MM-DD."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value
        return dt.datetime.fromtimestamp(secs, dt.timezone.utc).date().isoformat()
    s = str(value).strip()
    if s.isdigit():
        return to_iso_date(int(s))
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


@dataclass
class Listing:
    source: str
    source_id: str
    title: str
    price: float | None
    url: str | None = None
    currency: str = "USD"
    shipping: float | None = None
    is_sold: bool = False
    # True when the recorded price is the actual transaction price. False when the site
    # shows only the list price and accepted offers are hidden (Poshmark, eBay Best Offer).
    price_is_actual: bool | None = None
    sold_at: str | None = None
    listed_at: str | None = None
    brand: str | None = None
    size: str | None = None
    condition: str | None = None
    description: str | None = None
    age_hint: str | None = None  # site-provided era field, e.g. Etsy when_made
    image_url: str | None = None
    seller: str | None = None
    query: str | None = None
    raw: dict | None = None
    collected_at: str = field(default_factory=now_iso)

    def to_row(self) -> dict:
        return asdict(self)


def dig(d: Any, *paths: str, default: Any = None) -> Any:
    """Return the first non-empty value among dotted paths. Tolerates schema drift
    across undocumented APIs: list every field name a site has been seen to use."""
    for path in paths:
        cur = d
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return default


def to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("$", "").strip()
    try:
        return float(s.split()[0])
    except (ValueError, IndexError):
        return None
