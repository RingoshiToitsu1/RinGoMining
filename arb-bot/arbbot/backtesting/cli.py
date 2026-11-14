"""arbbot-backtest: replay recorded order books, or run a synthetic scenario.

  arbbot-backtest --synthetic 3 --steps 900 --latency 0.4 --fail-prob 0.05 --partial-prob 0.2
  arbbot-backtest --books var/books/2026-11-14.jsonl.gz      (uses verified mappings from DATABASE_URL)
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path

from ..config.runtime import RuntimeConfig
from ..config.settings import get_settings
from ..data.historical_data import load_books
from ..database.repo import Repository
from .engine import Backtester, PairSpec, demo_markets, synthetic_books


async def amain(a: argparse.Namespace) -> None:
    cfg = RuntimeConfig(kalshi_allocation=Decimal(a.kalshi), polymarket_allocation=Decimal(a.polymarket),
                        min_roi_pct=a.min_roi, max_roi_pct=a.max_roi, max_trade_usd=Decimal(a.max_trade),
                        max_unhedged_usd=Decimal(a.max_trade), kelly_fraction=a.kelly)
    pairs, books = [], []
    if a.books:
        repo = Repository(a.database_url or get_settings().database_url)
        loaded = await repo.load_config()
        cfg = loaded.model_copy(update={"kalshi_allocation": Decimal(a.kalshi),
                                        "polymarket_allocation": Decimal(a.polymarket)})
        for m in await repo.list_mappings(("verified",)):
            pairs.append(PairSpec(m.kalshi, m.polymarket, m.inverted, m.confidence, kalshi_resolution=None))
        await repo.close()
        wanted = {p.kalshi.market_id for p in pairs} | {p.polymarket.market_id for p in pairs}
        books = [b for b in load_books([Path(x) for x in a.books]) if b.market_id in wanted]
        if not pairs or not books:
            raise SystemExit("no verified mappings with recorded books found")
    else:
        for i in range(a.synthetic):
            k, p = demo_markets(i)
            pairs.append(PairSpec(k, p))
            books += synthetic_books(k, p, a.steps, seed=a.seed + i)
    bt = Backtester(cfg, pairs, books, kalshi_balance=Decimal(a.kalshi), polymarket_balance=Decimal(a.polymarket),
                    latency_s=a.latency, fail_prob=a.fail_prob, partial_prob=a.partial_prob, seed=a.seed)
    res = await bt.run()
    print(res.report())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--books", nargs="*", help="recorded order-book files (jsonl or jsonl.gz)")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--synthetic", type=int, default=2, help="number of synthetic market pairs")
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--kalshi", default="1000")
    ap.add_argument("--polymarket", default="1000")
    ap.add_argument("--max-trade", default="200")
    ap.add_argument("--min-roi", type=float, default=1.0)
    ap.add_argument("--max-roi", type=float, default=8.0)
    ap.add_argument("--kelly", type=float, default=0.25)
    ap.add_argument("--latency", type=float, default=0.3)
    ap.add_argument("--fail-prob", type=float, default=0.0)
    ap.add_argument("--partial-prob", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=7)
    asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    main()
