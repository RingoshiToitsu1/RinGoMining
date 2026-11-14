"""Order-book history: recorded live to gzip JSONL (one file per UTC day) and
replayed by the backtester. Market data is not a trading record, so it lives
on disk rather than in PostgreSQL."""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..core.types import OrderBook


class BookRecorder:
    def __init__(self, data_dir: Path, min_interval_s: float = 1.0, enabled: bool = True):
        self.dir = Path(data_dir) / "books"
        self.min_interval_s, self.enabled = min_interval_s, enabled
        self._last: dict[str, float] = {}
        self._fh = None
        self._day = ""

    def record(self, book: OrderBook) -> None:
        if not self.enabled:
            return
        key = f"{book.exchange.value}:{book.market_id}"
        if book.ts - self._last.get(key, 0.0) < self.min_interval_s:
            return
        self._last[key] = book.ts
        day = datetime.fromtimestamp(book.ts, timezone.utc).strftime("%Y-%m-%d")
        if day != self._day:
            self.close()
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = gzip.open(self.dir / f"{day}.jsonl.gz", "at")
            self._day = day
        self._fh.write(json.dumps(book.to_dict(), separators=(",", ":")) + "\n")

    def flush(self) -> None:
        if self._fh:
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def load_books(paths: list[Path]) -> Iterator[OrderBook]:
    """Yield recorded snapshots in time order across the given files."""
    rows = []
    for path in sorted(paths):
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    rows.sort(key=lambda r: r["ts"])
    for r in rows:
        yield OrderBook.from_dict(r)


def write_books(path: Path, books: list[OrderBook]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt") as fh:
        for b in books:
            fh.write(json.dumps(b.to_dict(), separators=(",", ":")) + "\n")


__all__ = ["BookRecorder", "load_books", "write_books", "time"]
