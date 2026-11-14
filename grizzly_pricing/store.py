from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .schema import Listing

COLUMNS = [
    "source", "source_id", "title", "price", "url", "currency", "shipping", "is_sold",
    "price_is_actual", "sold_at", "listed_at", "brand", "size", "condition", "description",
    "age_hint", "image_url", "seller", "query", "raw", "collected_at",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS listings (
  {", ".join(c + (" TEXT" if c not in ("price", "shipping", "is_sold", "price_is_actual") else
             (" REAL" if c in ("price", "shipping") else " INTEGER")) for c in COLUMNS)},
  first_seen TEXT,
  PRIMARY KEY (source, source_id)
);
-- Every price seen for a listing over time. Active listings snapshotted repeatedly
-- build forward-looking history for sites that hide sold data.
CREATE TABLE IF NOT EXISTS observations (
  source TEXT, source_id TEXT, collected_at TEXT, price REAL, is_sold INTEGER,
  PRIMARY KEY (source, source_id, collected_at)
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Several collectors (one per source) may write at once: WAL lets readers run during a
        # write, and the long timeout makes a writer wait for the lock instead of failing.
        self.db = sqlite3.connect(path, timeout=120)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def upsert(self, items: Iterable[Listing]) -> tuple[int, int]:
        new = seen = 0
        for it in items:
            row = it.to_row()
            row["raw"] = json.dumps(row["raw"], default=str)[:200_000] if row["raw"] else None
            prev = self.db.execute(
                "SELECT is_sold FROM listings WHERE source=? AND source_id=?",
                (it.source, it.source_id),
            ).fetchone()
            if prev is None:
                new += 1
                self.db.execute(
                    f"INSERT INTO listings ({','.join(COLUMNS)}, first_seen) VALUES "
                    f"({','.join('?' * len(COLUMNS))}, ?)",
                    [row[c] for c in COLUMNS] + [row["collected_at"]],
                )
            else:
                seen += 1
                # Never let an active re-sighting overwrite a recorded sale.
                if prev[0] and not it.is_sold:
                    pass
                else:
                    sets = ",".join(f"{c}=COALESCE(?, {c})" for c in COLUMNS[2:])
                    self.db.execute(
                        f"UPDATE listings SET {sets} WHERE source=? AND source_id=?",
                        [row[c] for c in COLUMNS[2:]] + [it.source, it.source_id],
                    )
            self.db.execute(
                "INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)",
                (it.source, it.source_id, row["collected_at"], it.price, int(it.is_sold)),
            )
            # Commit per item: `items` is a generator doing network I/O between yields, so a
            # transaction left open would hold the write lock for minutes and stall other sources.
            self.db.commit()
        self.db.commit()
        return new, seen

    def frame(self):
        import pandas as pd

        return pd.read_sql_query("SELECT * FROM listings", self.db)
