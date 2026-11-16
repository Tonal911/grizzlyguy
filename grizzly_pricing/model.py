"""Two models over sold, relevant, de-duplicated USD listings.

1. Hedonic ridge regression on log price: interpretable % effects per attribute, plus month
   coefficients, which give a quality-adjusted price index (the time trend after controlling
   for brand, age, leather and the rest). Source enters as a control, so Poshmark's
   list-price bias doesn't leak into the attribute effects or the trend.
2. Gradient-boosted quantile models (p10/p50/p90) for fair-price bands on a specific jacket.

Validation is time-based: train on the earlier 80% of sales, test on the latest 20%.
That's the honest check for "what will this sell for now".
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

log = logging.getLogger(__name__)

CAT = ["brand_norm", "leather_type", "collar", "style", "pattern", "color", "made_in",
       "condition_norm", "size_norm", "gender", "source", "decade_cat"]
NUM = ["vintage_kw", "zip_liner", "grizzly_kw"]
QUANTILES = (0.1, 0.5, 0.9)
MIN_ROWS = 60


def training_set(df: pd.DataFrame, history_days: int | None = 730, min_price: float | None = None) -> pd.DataFrame:
    """min_price: price floor applied to EVERY source. The Terapeak pages are saved with a
    $200 minimum, so modeling that segment means flooring all sources at $200 too; otherwise
    eBay looks inflated relative to sites whose cheap sales are kept."""
    if min_price is None:
        from .config import Config

        min_price = Config().min_price
    d = df[(df.is_sold == 1) & (df.relevant == 1) & (~df.dup.astype(bool)) & (df.currency == "USD")
           & df.price.between(min_price, 20000)].copy()
    if min_price < 200 and "ebay_terapeak" in set(d.source):
        log.warning("ebay_terapeak rows were saved with a $200 floor; a lower --min-price biases eBay upward")
    if history_days and d["event_date"].notna().any():
        # Poshmark sold search reaches back to 2015; a handful of old sales would anchor the
        # index on near-empty months. Keep the analysis window (default 2 years). Undated
        # rows (Vestiaire) are dropped later by fit() anyway.
        cutoff = d["event_date"].max() - pd.Timedelta(days=history_days)
        d = d[d["event_date"].isna() | (d["event_date"] >= cutoff)]
    d["decade_cat"] = d["decade"].map(lambda x: "unknown" if pd.isna(x) else str(int(x)))
    d["y"] = np.log(d["price"])
    return d


def _encoder(min_freq: int) -> OneHotEncoder:
    return OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=min_freq, sparse_output=False)


@dataclass
class Fitted:
    hedonic: Pipeline
    quantile: dict
    months: list[str]
    effects: pd.DataFrame
    index: pd.DataFrame
    metrics: dict = field(default_factory=dict)
    n: int = 0
    time_adj: dict = field(default_factory=dict)  # month -> log price level from hedonic index

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(self))

    @staticmethod
    def load(path: Path) -> "Fitted":
        return pickle.loads(path.read_bytes())

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        # Quantile models are trained on time-detrended prices; quote at the latest month's level.
        level = self.time_adj[self.months[-1]]
        out = {f"p{int(q * 100)}": np.exp(m.predict(X[CAT + NUM]) + level) for q, m in self.quantile.items()}
        out["hedonic"] = np.exp(self.hedonic.predict(X[CAT + NUM + ["month"]]))
        return pd.DataFrame(out, index=X.index)


def _hedonic(min_freq: int, alpha: float = 1.0) -> Pipeline:
    pre = ColumnTransformer([
        ("cat", _encoder(min_freq), CAT),
        ("month", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["month"]),
        ("num", "passthrough", NUM),
    ])
    return Pipeline([("pre", pre), ("ridge", Ridge(alpha=alpha))])


def _quantile(q: float, min_freq: int) -> Pipeline:
    pre = ColumnTransformer([("cat", _encoder(min_freq), CAT), ("num", "passthrough", NUM)])
    gbm = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300, learning_rate=0.05,
                                        max_leaf_nodes=15, min_samples_leaf=max(10, min_freq), random_state=0)
    return Pipeline([("pre", pre), ("gbm", gbm)])


def _effects(pipe: Pipeline, d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    pre, ridge = pipe.named_steps["pre"], pipe.named_steps["ridge"]
    names = pre.get_feature_names_out()
    coef = pd.Series(ridge.coef_, index=names)
    rows = []
    for c in CAT:
        sub = coef[coef.index.str.startswith(f"cat__{c}_")]
        centered = sub - sub.mean()  # effect vs the average level of this attribute
        for k, v in centered.items():
            level = k[len(f"cat__{c}_"):]
            n = int((d[c].astype(str) == level).sum()) if level != "infrequent_sklearn" else None
            rows.append({"feature": c, "level": level, "pct_effect": np.expm1(v) * 100, "n": n})
    for c in NUM:
        rows.append({"feature": c, "level": "1", "pct_effect": np.expm1(coef[f"num__{c}"]) * 100,
                     "n": int(d[c].sum())})
    eff = pd.DataFrame(rows).sort_values(["feature", "pct_effect"], ascending=[True, False])
    mon = coef[coef.index.str.startswith("month__month_")]
    mon.index = mon.index.str.replace("month__month_", "", regex=False)
    mon = mon.sort_index()
    counts = d.groupby("month").size()
    idx = pd.DataFrame({"month": mon.index, "index": np.exp(mon.values - mon.values[0]) * 100,
                        "n_sales": counts.reindex(mon.index).fillna(0).astype(int).values,
                        "median_price": d.groupby("month").price.median().reindex(mon.index).values})
    return eff, idx


def _time_adj(pipe: Pipeline) -> dict:
    names = pipe.named_steps["pre"].get_feature_names_out()
    coef = pd.Series(pipe.named_steps["ridge"].coef_, index=names)
    mon = coef[coef.index.str.startswith("month__month_")]
    return {k.replace("month__month_", ""): float(v) for k, v in mon.items()}


def fit(df: pd.DataFrame, min_price: float | None = None) -> Fitted:
    d = training_set(df, min_price=min_price).dropna(subset=["month"])
    if len(d) < MIN_ROWS:
        raise ValueError(f"only {len(d)} usable sold rows; need >= {MIN_ROWS}. Collect more first.")
    min_freq = max(5, len(d) // 200)
    d = d.sort_values("event_date")
    cut = int(len(d) * 0.8)
    train, test = d.iloc[:cut], d.iloc[cut:]

    metrics = {}
    h = _hedonic(min_freq).fit(train[CAT + NUM + ["month"]], train.y)
    # Test months are unseen by construction; predict them at the last trained month's level.
    t_x = test[CAT + NUM + ["month"]].copy()
    t_x["month"] = train["month"].iloc[-1]
    err = np.abs(np.expm1(h.predict(t_x) - test.y))
    metrics["hedonic_median_abs_pct_err"] = float(np.median(err) * 100)
    adj = _time_adj(h)
    y_detr = train.y - train.month.map(adj)
    qs = {q: _quantile(q, min_freq).fit(train[CAT + NUM], y_detr) for q in QUANTILES}
    last = adj[train["month"].iloc[-1]]
    lo, mid, hi = (qs[q].predict(test[CAT + NUM]) + last for q in QUANTILES)
    metrics["gbm_median_abs_pct_err"] = float(np.median(np.abs(np.expm1(mid - test.y))) * 100)
    metrics["p10_p90_coverage_pct"] = float(np.mean((test.y >= lo) & (test.y <= hi)) * 100)
    # Baseline: median of the most recent 3 training months (what a careful human would quote).
    recent = train[train.month >= sorted(train.month.unique())[-3]]
    metrics["baseline_median_abs_pct_err"] = float(np.median(np.abs(np.expm1(recent.y.median() - test.y))) * 100)
    metrics.update(n_train=len(train), n_test=len(test))

    # Refit on everything for the deliverable.
    h = _hedonic(min_freq).fit(d[CAT + NUM + ["month"]], d.y)
    adj = _time_adj(h)
    qs = {q: _quantile(q, min_freq).fit(d[CAT + NUM], d.y - d.month.map(adj)) for q in QUANTILES}
    eff, idx = _effects(h, d)
    return Fitted(h, qs, sorted(d.month.unique()), eff, idx, metrics, len(d), adj)


def comps(df: pd.DataFrame, row: dict, k: int = 10) -> pd.DataFrame:
    """Nearest sold comparables by title-token Jaccard, same brand boosted."""
    import re

    toks = lambda s: set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))
    q = toks(row.get("title"))
    d = training_set(df)
    if d.empty:
        return d
    score = d.title.map(lambda t: len(q & toks(t)) / max(1, len(q | toks(t))))
    score += (d.brand_norm == row.get("brand_norm")).astype(float) * 0.25
    return d.assign(score=score).nlargest(k, "score")[["source", "title", "price", "sold_at", "url", "score"]]
