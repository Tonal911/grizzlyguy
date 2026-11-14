"""Scripts to work out what's reachable before a full collection run.

connectivity - one request per source host; reports status / bot wall.
sample       - run each collector for one page, save raw items, print field coverage. If a
               field shows 0%, the mapping in that source module needs the real key name;
               the saved JSON shows it.
discover     - open a page in a real browser and log every JSON API it calls. Use when a
               source breaks or to find a new site's internal API.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from itertools import islice

from .config import Config
from .net import Blocked, Http
from .sources import SOURCES

log = logging.getLogger(__name__)

KEY_FIELDS = ["title", "price", "is_sold", "sold_at", "listed_at", "brand", "size", "condition"]


def connectivity(cfg: Config) -> list[dict]:
    http = Http(0.2, 0.5, retries=0)
    out = []
    for s in SOURCES.values():
        url = f"https://{s.host}/"
        try:
            r = http.get(url, timeout=15)
            status = str(r.status_code)
        except Blocked as e:
            status = f"BOT WALL ({e})"
        except Exception as e:
            status = f"ERROR {type(e).__name__}: {str(e)[:80]}"
        out.append({"source": s.name, "host": s.host, "status": status})
    return out


def sample(cfg: Config, names: list[str], n: int = 30) -> dict:
    cfg = replace(cfg, max_pages=1)
    http = Http(cfg.min_delay, cfg.max_delay)
    cfg.probe_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for name in names:
        src = SOURCES[name]
        try:
            items = list(islice(src.collect(http, cfg.queries[0], cfg), n))
        except Exception as e:
            report[name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        (cfg.probe_dir / f"{name}.json").write_text(json.dumps([i.to_row() for i in items], indent=1, default=str))
        cov = {f: f"{100 * sum(1 for i in items if getattr(i, f) not in (None, '', False)) / max(1, len(items)):.0f}%"
               for f in KEY_FIELDS}
        prices = sorted(i.price for i in items if i.price)
        report[name] = {"items": len(items), "coverage": cov,
                        "price_min_med_max": [prices[0], prices[len(prices) // 2], prices[-1]] if prices else None}
    return report
