"""Import eBay sold history exported by hand from Terapeak.

Terapeak Product Research is free for eBay accounts (Seller Hub -> Research) and covers up
to 3 years of sold listings. It has no API or export button. Workflow:
  1. Seller Hub -> Research -> Product research. Search a query, set the range to 2 years,
     open the "Sold" tab.
  2. Select the results table, copy, paste into a spreadsheet, save as CSV. Or save the page
     (Ctrl+S, "Webpage, HTML only").
  3. `python -m grizzly_pricing import-terapeak <file> --query "<query>"`
Repeat with narrower queries or shorter date ranges where a search hits its result cap.

Column matching is by header text, so small label changes don't break the import.
"""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from bs4 import BeautifulSoup

from ..schema import Listing, to_float, to_iso_date

COLS = {
    "title": ("listing", "title", "item"),
    "price": ("avg sold price", "sold price", "price"),
    "shipping": ("avg shipping", "shipping"),
    "sold_at": ("date last sold", "last sold", "sold date", "date"),
    "qty": ("total sold", "quantity", "qty"),
    "url": ("url", "link"),
}


def _map_headers(headers: list[str]) -> dict[str, int]:
    low = [h.strip().lower() for h in headers]
    out: dict[str, int] = {}
    for key, cands in COLS.items():
        for cand in cands:
            idx = next((i for i, h in enumerate(low) if cand in h and i not in out.values()), None)
            if idx is not None:
                out[key] = idx
                break
    if "title" not in out or "price" not in out:
        raise ValueError(f"need title and price columns; got headers {headers}")
    return out


def _rows_from_file(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".html", ".htm"):
        soup = BeautifulSoup(text, "html.parser")
        best = max(soup.find_all("table"), key=lambda t: len(t.find_all("tr")), default=None)
        if best is None:
            raise ValueError("no <table> in saved page")
        return [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                for tr in best.find_all("tr")]
    dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
    return list(csv.reader(text.splitlines(), dialect=dialect))


def import_file(path: str | Path, query: str | None = None) -> list[Listing]:
    rows = _rows_from_file(Path(path))
    cols = _map_headers(rows[0])
    out = []
    for r in rows[1:]:
        get = lambda k: r[cols[k]].strip() if k in cols and cols[k] < len(r) else None
        title = get("title")
        if not title:
            continue
        # Terapeak cells often hold "Title\nItem ID 1234..." - pull the eBay id if present.
        m = re.search(r"\b(\d{12})\b", " ".join(r))
        sold_at = to_iso_date(get("sold_at")) if get("sold_at") else None
        sid = m.group(1) if m else hashlib.sha1(f"{title}|{get('price')}|{sold_at}".encode()).hexdigest()[:16]
        out.append(Listing(
            source="ebay", source_id=sid, title=re.sub(r"\s+", " ", title),
            price=to_float(get("price")), shipping=to_float(get("shipping")),
            url=f"https://www.ebay.com/itm/{m.group(1)}" if m else get("url"),
            is_sold=True, price_is_actual=True, sold_at=sold_at, query=query,
            raw={"terapeak_row": r, "qty": get("qty")},
        ))
    return out
