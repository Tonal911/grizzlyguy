from __future__ import annotations

from pathlib import Path

import pandas as pd

from .model import Fitted, training_set


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("source")
    return pd.DataFrame({
        "rows": g.size(),
        "sold": g.is_sold.sum(),
        "relevant_sold": g.apply(lambda x: int(((x.is_sold == 1) & (x.relevant == 1) & ~x.dup.astype(bool)).sum())),
        "first_sale": g.apply(lambda x: x.loc[x.is_sold == 1, "sold_at"].min()),
        "last_sale": g.apply(lambda x: x.loc[x.is_sold == 1, "sold_at"].max()),
        "median_sold_price": g.apply(lambda x: x.loc[x.is_sold == 1, "price"].median()),
        "median_ask": g.apply(lambda x: x.loc[x.is_sold == 0, "price"].median()),
    })


def write(df: pd.DataFrame, fitted: Fitted | None, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    cov = coverage(df)
    cov.to_csv(out / "coverage.csv")
    t = training_set(df)
    df.to_csv(out / "all_listings_with_features.csv", index=False)
    # Hand-check sample: relevance rules decide what the model sees, so audit them.
    df.sample(min(200, len(df)), random_state=0)[
        ["source", "title", "price", "is_sold", "relevant", "brand_norm", "decade", "collar", "leather_type", "url"]
    ].to_csv(out / "review_sample.csv", index=False)
    lines = ["# Grizzly leather jacket market report", "",
             f"Listings: {len(df)}; sold+relevant+deduped (model set): {len(t)}", "",
             "## Coverage by source", "", cov.to_markdown(), ""]
    if fitted:
        fitted.effects.to_csv(out / "effects.csv", index=False)
        fitted.index.to_csv(out / "price_index.csv", index=False)
        lines += ["## Validation (train on oldest 80% of sales, test on newest 20%)", ""]
        lines += [f"- {k}: {v:.1f}" if isinstance(v, float) else f"- {k}: {v}" for k, v in fitted.metrics.items()]
        lines += ["", "Compare model errors with the baseline (median of the last 3 training months). A model that "
                  "doesn't beat the baseline clearly has too little data or too-noisy relevance filtering.", "",
                  "## Quality-adjusted price index (first month = 100)", "",
                  fitted.index.to_markdown(index=False), "",
                  "## Attribute effects (% vs the average level of that attribute, controlling for all others)", ""]
        for feat, g in fitted.effects.groupby("feature"):
            lines += [f"### {feat}", "", g.drop(columns="feature").to_markdown(index=False, floatfmt=".1f"), ""]
    p = out / "report.md"
    p.write_text("\n".join(lines))
    return p
