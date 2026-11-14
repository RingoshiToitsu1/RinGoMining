"""Historical backtesting.

Replays recorded (or synthetic) order-book snapshots through the *same*
profitability engine, capital allocator, risk manager and execution state
machine used live, against simulated exchanges with configurable latency,
partial fills and order failures. Orders fill against the book as it stands
after the simulated latency, so quotes can move away mid-execution.
"""
from __future__ import annotations

import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..config.runtime import RuntimeConfig
from ..core.arbitrage_engine import ArbitrageEngine
from ..core.capital_allocator import CapitalAllocator
from ..core.events import Event, EventBus
from ..core.execution_engine import ExecutionEngine
from ..core.profitability import ProfitabilityEngine
from ..core.risk_manager import RiskManager
from ..core.types import ONE, ZERO, Exchange, FeeSpec, Level, Market, OrderBook, Resolution
from ..data.orderbooks import BookFeed, OrderBookStore
from ..database.repo import Repository
from ..exchanges.sim import SimExchange
from ..infra.cache import MemoryCache

D = Decimal


@dataclass
class PairSpec:
    kalshi: Market
    polymarket: Market
    inverted: bool = False
    confidence: float = 100.0
    # how the pair finally resolves (YES on Kalshi's contract); None leaves positions open
    kalshi_resolution: Resolution | None = Resolution.YES


@dataclass
class BacktestResult:
    snapshots: int = 0
    scans: int = 0
    executions: int = 0
    matched: int = 0
    failed: int = 0
    partial: int = 0
    hedges: int = 0
    unwinds: int = 0
    naked: int = 0
    fees: Decimal = ZERO
    capital_peak: Decimal = ZERO
    locked_in: Decimal = ZERO
    realized: Decimal = ZERO
    end_equity: Decimal = ZERO
    start_equity: Decimal = ZERO
    accepted: int = 0
    rejections: Counter = field(default_factory=Counter)
    pauses: dict = field(default_factory=dict)

    @property
    def net(self) -> Decimal:
        return self.end_equity - self.start_equity

    def report(self) -> str:
        top = "\n".join(f"    {n:>5}  {r}" for r, n in self.rejections.most_common(8)) or "    none"
        return (
            f"snapshots replayed      {self.snapshots}\n"
            f"scans                   {self.scans}\n"
            f"opportunities accepted  {self.accepted}\n"
            f"executions              {self.executions}  (matched {self.matched}, failed {self.failed}, "
            f"partial {self.partial})\n"
            f"emergency hedges        {self.hedges}\n"
            f"emergency unwinds       {self.unwinds}\n"
            f"unhedged residuals      {self.naked}\n"
            f"fees paid               ${self.fees:.2f}\n"
            f"peak capital deployed   ${self.capital_peak:.2f}\n"
            f"realized P&L            ${self.realized:.2f}\n"
            f"locked in (unsettled)   ${self.locked_in:.2f}\n"
            f"equity                  ${self.start_equity:.2f} -> ${self.end_equity:.2f}  (net ${self.net:.2f})\n"
            f"risk pauses at end      {', '.join(self.pauses) or 'none'}\n"
            f"rejections by reason:\n{top}")


class Backtester:
    def __init__(self, cfg: RuntimeConfig, pairs: list[PairSpec], books: list[OrderBook], *,
                 kalshi_balance: Decimal = D("1000"), polymarket_balance: Decimal = D("1000"),
                 latency_s: float = 0.3, fail_prob: float = 0.0, partial_prob: float = 0.0, seed: int = 7,
                 scan_every_s: float = 1.0):
        self.cfg, self.pairs = cfg, pairs
        self.books = sorted(books, key=lambda b: b.ts)
        self.kalshi_balance, self.polymarket_balance = D(kalshi_balance), D(polymarket_balance)
        self.latency_s, self.fail_prob, self.partial_prob, self.seed = latency_s, fail_prob, partial_prob, seed
        self.scan_every_s = scan_every_s
        self._i = 0
        self._t = self.books[0].ts if self.books else 0.0

    def _apply_until(self, t: float) -> int:
        n = 0
        while self._i < len(self.books) and self.books[self._i].ts <= t:
            b = self.books[self._i]
            self.sims[b.exchange].books[b.market_id] = OrderBook.from_dict(b.to_dict())
            self._i += 1
            n += 1
        self._t = max(self._t, t)
        return n

    async def _latency(self, seconds: float) -> None:
        """Simulated latency advances the replay: the market keeps moving while an order is in flight."""
        self._apply_until(self._t + seconds)

    async def run(self) -> BacktestResult:
        cfg = self.cfg
        cfg.mode, cfg.autotrade, cfg.notify_opportunities = "paper", True, False
        repo = Repository("sqlite+aiosqlite:///:memory:")
        await repo.create_all()
        bus = EventBus()
        res = BacktestResult(start_equity=self.kalshi_balance + self.polymarket_balance)

        async def count(ev: Event) -> None:
            if ev.topic == "execution.emergency":
                res.hedges += ev.data["kind"] == "hedge"
                res.unwinds += ev.data["kind"] == "unwind"

        bus.subscribe("execution.emergency", count)
        mk = dict(latency_s=self.latency_s, fail_prob=self.fail_prob, partial_prob=self.partial_prob,
                  sleep=self._latency)
        self.sims = {Exchange.KALSHI: SimExchange(Exchange.KALSHI, self.kalshi_balance, seed=self.seed, **mk),
                     Exchange.POLYMARKET: SimExchange(Exchange.POLYMARKET, self.polymarket_balance,
                                                      seed=self.seed + 1, **mk)}
        venues = dict(self.sims)
        cache = MemoryCache()
        store = OrderBookStore()
        feed = BookFeed(venues, store, bus, lambda: 0.0)
        profit = ProfitabilityEngine(cfg)
        allocator = CapitalAllocator(cfg, repo, venues, profit)
        risk = RiskManager(cfg, repo, bus, allocator, store, venues)
        execution = ExecutionEngine(cfg, repo, bus, venues, store, allocator, risk, profit, cache, retry_delay_s=0.0)
        engine = ArbitrageEngine(cfg, repo, bus, venues, store, feed, allocator, risk, execution)
        for spec in self.pairs:
            self.sims[Exchange.KALSHI].add_market(spec.kalshi)
            self.sims[Exchange.POLYMARKET].add_market(spec.polymarket)
            await repo.upsert_markets([spec.kalshi, spec.polymarket])
            await repo.save_mapping(spec.kalshi, spec.polymarket, inverted=spec.inverted, confidence=spec.confidence,
                                    settlement_compatible=True, status="verified", method="backtest", checks={})
        await engine.reload_mappings()

        next_scan = self._t
        while self._i < len(self.books):
            res.snapshots += self._apply_until(self.books[self._i].ts)
            if self._t < next_scan:
                continue
            next_scan = self._t + self.scan_every_s
            for ex in venues:
                try:
                    await feed.poll_once(ex)
                except Exception:
                    pass
            await allocator.refresh_balances()
            opps = await engine.scan_once()
            res.scans += 1
            for o in opps:
                if o.evaluation.accepted:
                    res.accepted += 1
                elif o.evaluation.quote is not None:
                    res.rejections[re.sub(r"-?\$?\d[\d.,]*%?", "N", o.evaluation.reason)[:70]] += 1
            dep = await allocator.deployed()
            res.capital_peak = max(res.capital_peak, sum(dep.values(), ZERO))

        # settle according to each pair's resolution
        for spec in self.pairs:
            if spec.kalshi_resolution is None:
                continue
            ky = ONE if spec.kalshi_resolution is Resolution.YES else ZERO
            py = ONE - ky if spec.inverted else ky
            self.sims[Exchange.KALSHI].resolutions[spec.kalshi.market_id] = (spec.kalshi_resolution, ky)
            self.sims[Exchange.POLYMARKET].resolutions[spec.polymarket.market_id] = (
                Resolution.YES if py == ONE else Resolution.NO, py)
        await engine.settle_once()

        st = await repo.stats("paper")
        res.executions, res.matched, res.failed = st["total_trades"], st["matched_pairs"], st["failed"]
        res.partial, res.fees, res.realized, res.locked_in = st["partial_fills"], st["fees"], st["net_realized"], st["locked_in"]
        open_pos = await repo.open_positions("paper")
        res.naked = sum(1 for p in open_pos if p.kind == "naked")
        res.end_equity = sum((s.cash for s in self.sims.values()), ZERO) + sum((p.cost_basis for p in open_pos), ZERO)
        res.pauses = dict(risk.pauses)
        await repo.close()
        return res


def demo_markets(i: int = 0, days: int = 20) -> tuple[Market, Market]:
    close = datetime.now(timezone.utc) + timedelta(days=days)
    k = Market(Exchange.KALSHI, f"KXDEMO-{i}", f"Synthetic event {i}", close_time=close, expiration_time=close,
               fee=FeeSpec("kalshi_quadratic", D("0.07")))
    p = Market(Exchange.POLYMARKET, f"demo-event-{i}", f"Synthetic event {i}", close_time=close,
               expiration_time=close, fee=FeeSpec("notional_bps", D("0.005")))
    return k, p


def synthetic_books(k: Market, p: Market, steps: int = 600, seed: int = 1, *, dt: float = 1.0,
                    dislocation_prob: float = 0.06, depth: tuple[int, int] = (50, 400)) -> list[OrderBook]:
    """A fair value random walk quoted on both venues. Most of the time the venues agree
    (no arbitrage). Occasionally one lags or overshoots for a few seconds, which is the
    dislocation an arbitrageur trades."""
    rng = random.Random(seed)
    t0 = time.time() - steps * dt
    fair, gap, gap_left = 0.5, 0.0, 0
    out: list[OrderBook] = []

    def ladder(mid: float, spread: float) -> tuple[list[Level], list[Level]]:
        bids, asks = [], []
        for lvl in range(4):
            size = D(rng.randint(*depth))
            b = round(mid - spread / 2 - 0.01 * lvl, 2)
            a = round(mid + spread / 2 + 0.01 * lvl, 2)
            if 0.01 <= b <= 0.99:
                bids.append(Level(D(str(b)), size))
            if 0.01 <= a <= 0.99:
                asks.append(Level(D(str(a)), size))
        return bids, asks

    for i in range(steps):
        fair = min(max(fair + rng.gauss(0, 0.0012), 0.08), 0.92)
        if gap_left <= 0 and rng.random() < dislocation_prob:
            gap, gap_left = rng.choice([-1, 1]) * rng.uniform(0.03, 0.09), rng.randint(2, 8)
        if gap_left > 0:
            gap_left -= 1
        else:
            gap = 0.0
        ts = t0 + i * dt
        kb, ka = ladder(fair, 0.02)
        pb, pa = ladder(fair + gap, 0.02)
        out.append(OrderBook.from_single_book(Exchange.KALSHI, k.market_id, kb, ka, ts=ts))
        out.append(OrderBook.from_single_book(Exchange.POLYMARKET, p.market_id, pb, pa, ts=ts + dt / 2))
    return out
