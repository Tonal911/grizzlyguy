from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace

from .config import Config
from .net import Blocked, Http

log = logging.getLogger("grizzly")


def keep(items, min_price: float, skipped: dict):
    """Store only relevant listings at or above the price floor (no junk in the DB)."""
    from .features import extract

    for it in items:
        if it.price is not None and it.price >= min_price and extract(it.to_row())["relevant"]:
            yield it
        else:
            skipped["n"] += 1


def cmd_prune(cfg: Config, a) -> None:
    """Delete stored rows that `keep` would reject (collected before the floor existed)."""
    from .features import extract
    from .store import Store

    st = Store(cfg.db_path)
    rows = st.db.execute("SELECT source, source_id, title, description, brand, price FROM listings").fetchall()
    junk = [(r[0], r[1]) for r in rows
            if r[5] is None or r[5] < cfg.min_price
            or not extract({"title": r[2], "description": r[3], "brand": r[4]})["relevant"]]
    print(f"{len(junk)} of {len(rows)} rows are below ${cfg.min_price:g} or not relevant")
    if not a.yes:
        print("dry run; add --yes to delete them")
        return
    st.db.executemany("DELETE FROM listings WHERE source=? AND source_id=?", junk)
    st.db.executemany("DELETE FROM observations WHERE source=? AND source_id=?", junk)
    st.db.commit()
    st.db.execute("VACUUM")
    print(f"deleted {len(junk)} rows")


def cmd_collect(cfg: Config, a) -> None:
    from .sources import SOURCES
    from .store import Store

    names = list(SOURCES) if a.sources == ["all"] else a.sources
    store, http = Store(cfg.db_path), Http(cfg.min_delay, cfg.max_delay)
    for name in names:
        src = SOURCES[name]
        for q in cfg.queries:
            try:
                kept = keep(src.collect(http, q, cfg), cfg.min_price, skipped := {"n": 0})
                new, seen = store.upsert(kept)
                log.info("%-20s %-40s new=%d updated=%d skipped=%d", name, q, new, seen, skipped["n"])
            except Blocked as e:
                log.error("%s blocked: %s. Retry with --headful, or later.", name, e)
                break
            except Exception as e:
                log.error("%s '%s' failed: %s: %s", name, q, type(e).__name__, e)


def cmd_import(cfg: Config, a) -> None:
    from .sources.terapeak import import_file
    from .store import Store

    items = import_file(a.path, a.query)
    new, seen = Store(cfg.db_path).upsert(items)
    print(f"imported {len(items)} rows: new={new} updated={seen}")


def _frame(cfg: Config):
    from .features import build_frame
    from .store import Store

    df = Store(cfg.db_path).frame()
    if df.empty:
        sys.exit("no data yet; run collect or import-terapeak")
    return build_frame(df)


def cmd_train(cfg: Config, a) -> None:
    from .model import fit
    from .report import write

    df = _frame(cfg)
    fitted = None
    try:
        fitted = fit(df, min_price=cfg.min_price if a.min_price is None else a.min_price)
        fitted.save(cfg.report_dir / "model.pkl")
        print(json.dumps(fitted.metrics, indent=1))
    except ValueError as e:
        log.warning("model not trained: %s", e)
    print("report:", write(df, fitted, cfg.report_dir))


def cmd_estimate(cfg: Config, a) -> None:
    import pandas as pd

    from .features import extract
    from .model import Fitted, comps

    fitted = Fitted.load(cfg.report_dir / "model.pkl")
    row = {"title": a.title, "description": a.description, "brand": a.brand, "size": a.size,
           "condition": a.condition}
    f = extract(row)
    x = pd.DataFrame([{**row, **f, "source": a.source, "month": fitted.months[-1],
                       "decade_cat": "unknown" if pd.isna(f["decade"]) else str(int(f["decade"]))}])
    pred = fitted.predict(x).iloc[0]
    print("extracted:", {k: v for k, v in f.items() if k != "relevant"})
    print(f"fair price (as sold on {a.source}, current month): "
          f"p10 ${pred.p10:,.0f} | median ${pred.p50:,.0f} | p90 ${pred.p90:,.0f} | hedonic ${pred.hedonic:,.0f}")
    print(comps(_frame(cfg), {"title": a.title, "brand_norm": f["brand_norm"]}).to_string(index=False))


def cmd_probe(cfg: Config, a) -> None:
    from . import probe
    from .sources import SOURCES

    if a.what == "connectivity":
        for r in probe.connectivity(cfg):
            print(f"{r['source']:<22}{r['host']:<36}{r['status']}")
    elif a.what == "sample":
        names = list(SOURCES) if a.sources == ["all"] else a.sources
        print(json.dumps(probe.sample(cfg, names), indent=1))
        print(f"raw samples in {cfg.probe_dir}")
    else:
        from .browser import discover

        if not a.url:
            sys.exit("--url required")
        for r in discover(a.url, cfg.probe_dir / "discover", headless=cfg.headless):
            print(r)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="grizzly_pricing")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--headful", action="store_true", help="visible browser (solve captchas by hand)")
    p.add_argument("--query", action="append", dest="queries", help="override search queries (repeatable)")
    p.add_argument("--max-pages", type=int)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect")
    c.add_argument("--sources", nargs="+", default=["all"])
    i = sub.add_parser("import-terapeak")
    i.add_argument("path")
    i.add_argument("--query")
    tr = sub.add_parser("train")
    tr.add_argument("--min-price", type=float, default=None,
                    help="price floor for every source (default: GRIZZLY_MIN_PRICE, 200)")
    pr2 = sub.add_parser("prune", help="delete stored rows below the price floor or not relevant")
    pr2.add_argument("--yes", action="store_true")
    e = sub.add_parser("estimate")
    e.add_argument("title")
    e.add_argument("--description")
    e.add_argument("--brand")
    e.add_argument("--size")
    e.add_argument("--condition")
    e.add_argument("--source", default="ebay", help="price level to quote: ebay, grailed, poshmark, ...")
    pr = sub.add_parser("probe")
    pr.add_argument("what", choices=["connectivity", "sample", "discover"])
    pr.add_argument("--sources", nargs="+", default=["all"])
    pr.add_argument("--url")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config(headless=not a.headful)
    if a.queries:
        cfg = replace(cfg, queries=a.queries)
    if a.max_pages:
        cfg = replace(cfg, max_pages=a.max_pages)
    {"collect": cmd_collect, "prune": cmd_prune, "import-terapeak": cmd_import, "train": cmd_train,
     "estimate": cmd_estimate, "probe": cmd_probe}[a.cmd](cfg, a)


if __name__ == "__main__":
    main()
