from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(os.environ.get("GRIZZLY_DATA_DIR", Path(__file__).resolve().parent / "data"))

# "Grizzly" here means the style: heavy leather jacket with a fur or shearling collar.
# Sellers rarely use the word, so the style is searched under its common synonyms too.
DEFAULT_QUERIES = [
    "grizzly leather jacket",
    "leather jacket fur collar",
    "leather jacket shearling collar",
    "leather bomber fur collar",
    "leather coat fur collar",
    "leather jacket raccoon collar",
    "leather jacket mouton collar",
]


@dataclass
class Config:
    queries: list[str] = field(default_factory=lambda: list(DEFAULT_QUERIES))
    history_days: int = 730
    # Floor for every source, at collection and in the model. Sub-$200 sales are mostly
    # mall/fast-fashion jackets; Terapeak pages are saved with the same $200 filter.
    min_price: float = float(os.environ.get("GRIZZLY_MIN_PRICE", 200))
    max_pages: int = 20
    min_delay: float = 2.0
    max_delay: float = 5.0
    # eBay category ids: 57988 Men's Coats, Jackets & Vests; 63862 Women's Coats, Jackets & Vests.
    ebay_category_ids: list[str] = field(default_factory=lambda: ["57988", "63862"])
    headless: bool = True
    db_path: Path = field(default_factory=lambda: DATA_DIR / "grizzly.db")

    @property
    def probe_dir(self) -> Path:
        return DATA_DIR / "probe"

    @property
    def report_dir(self) -> Path:
        return DATA_DIR / "report"


def env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v else None
