from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arbbot.config.runtime import RuntimeConfig
from arbbot.core.arbitrage_engine import ArbitrageEngine
from arbbot.core.capital_allocator import CapitalAllocator
from arbbot.core.events import Event, EventBus
from arbbot.core.execution_engine import ExecutionEngine
from arbbot.core.market_matcher import MarketMatcher
from arbbot.core.profitability import ProfitabilityEngine
from arbbot.core.risk_manager import RiskManager
from arbbot.core.types import Exchange, FeeSpec, Mapping, Market
from arbbot.data.orderbooks import BookFeed, OrderBookStore
from arbbot.database.repo import Repository
from arbbot.exchanges.sim import SimExchange
from arbbot.infra.cache import MemoryCache

D = Decimal
CLOSE = datetime.now(timezone.utc) + timedelta(days=10)


def kalshi_market(mid: str = "KXBTC-100K", title: str = "Will Bitcoin be above $100,000 on Dec 31, 2026?") -> Market:
    return Market(Exchange.KALSHI, mid, title, close_time=CLOSE, expiration_time=CLOSE,
                  rules="Resolves Yes if the CF Benchmarks Bitcoin price is above 100000.",
                  fee=FeeSpec("kalshi_quadratic", D("0.07")))


def poly_market(mid: str = "btc-100k-dec-31", title: str = "Bitcoin above $100,000 on December 31, 2026?") -> Market:
    return Market(Exchange.POLYMARKET, mid, title, close_time=CLOSE, expiration_time=CLOSE,
                  rules="Resolves Yes if the CF Benchmarks Bitcoin price is above 100000.",
                  fee=FeeSpec("notional_bps", D("0.001")))


async def fresh_repo() -> Repository:
    """SQLite in memory by default; set TEST_DATABASE_URL to run the suite on PostgreSQL."""
    url = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    repo = Repository(url)
    if not url.startswith("sqlite"):
        from arbbot.database.models import Base
        async with repo.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await repo.create_all()
    return repo


def fresh_cache():
    """In-process by default; set TEST_REDIS_URL to run locks and caching on Redis."""
    url = os.environ.get("TEST_REDIS_URL", "")
    if not url:
        return MemoryCache()
    from arbbot.infra.cache import RedisCache
    return RedisCache(url, prefix=f"arbbot-test-{os.getpid()}-{id(object())}:")


@dataclass
class Harness:
    cfg: RuntimeConfig
    repo: Repository
    bus: EventBus
    kalshi: SimExchange
    poly: SimExchange
    venues: dict
    store: OrderBookStore
    feed: BookFeed
    profit: ProfitabilityEngine
    allocator: CapitalAllocator
    risk: RiskManager
    execution: ExecutionEngine
    engine: ArbitrageEngine
    matcher: MarketMatcher
    events: list
    mapping: Mapping | None = None

    def topics(self) -> list[str]:
        return [e.topic for e in self.events]

    def of(self, topic: str) -> list[Event]:
        return [e for e in self.events if e.topic == topic]

    async def add_mapping(self, k: Market | None = None, p: Market | None = None, *, inverted: bool = False,
                          confidence: float = 100.0, status: str = "verified", compatible: bool = True) -> Mapping:
        k, p = k or kalshi_market(), p or poly_market()
        self.kalshi.add_market(k)
        self.poly.add_market(p)
        await self.repo.upsert_markets([k, p])
        mid = await self.repo.save_mapping(k, p, inverted=inverted, confidence=confidence,
                                           settlement_compatible=compatible, status=status, method="manual", checks={})
        self.mapping = await self.repo.get_mapping(mid)
        await self.engine.reload_mappings()
        return self.mapping

    def books(self, k_yes_ask="0.40", p_no_ask="0.50", size="200", k_mid=None, p_mid=None) -> None:
        """Kalshi YES ask + Polymarket NO ask < 1 is the arbitrage. Bids sit 2c under asks."""
        k_mid = k_mid or self.mapping.kalshi.market_id
        p_mid = p_mid or self.mapping.polymarket.market_id
        ka, pn = D(k_yes_ask), D(p_no_ask)
        self.kalshi.set_book(k_mid, yes_bids=[(ka - D("0.02"), size)], yes_asks=[(ka, size)])
        # polymarket NO ask at pn  <=>  YES bid at 1-pn
        self.poly.set_book(p_mid, yes_bids=[(1 - pn, size)], yes_asks=[(1 - pn + D("0.02"), size)])

    async def pump(self) -> None:
        """Pull fresh books into the store and verify balances, as the live loops do."""
        for ex in self.venues:
            await self.feed.poll_once(ex)
        await self.allocator.refresh_balances()


@pytest.fixture
async def h() -> Harness:
    cfg = RuntimeConfig(kalshi_allocation=D("1000"), polymarket_allocation=D("1000"), autotrade=True,
                        max_trade_usd=D("200"), max_unhedged_usd=D("200"), max_unhedged_seconds=0.3,
                        max_execution_seconds=5, notify_opportunities=True)
    repo = await fresh_repo()
    bus = EventBus()
    seen: list[Event] = []

    async def record(ev: Event) -> None:
        seen.append(ev)

    for prefix in ("market.*", "signal.*", "execution.*", "position.*", "risk.*", "exchange.*"):
        bus.subscribe(prefix, record)
    kalshi = SimExchange(Exchange.KALSHI, D("1000"), seed=1)
    poly = SimExchange(Exchange.POLYMARKET, D("1000"), seed=2)
    venues = {Exchange.KALSHI: kalshi, Exchange.POLYMARKET: poly}
    cache = fresh_cache()
    store = OrderBookStore(cache)
    feed = BookFeed(venues, store, bus, lambda: 0.01)
    profit = ProfitabilityEngine(cfg)
    allocator = CapitalAllocator(cfg, repo, venues, profit)
    risk = RiskManager(cfg, repo, bus, allocator, store, venues)
    execution = ExecutionEngine(cfg, repo, bus, venues, store, allocator, risk, profit, cache, retry_delay_s=0.01)
    risk._busy = lambda: execution.busy
    engine = ArbitrageEngine(cfg, repo, bus, venues, store, feed, allocator, risk, execution)
    matcher = MarketMatcher(cfg, repo, bus)
    harness = Harness(cfg, repo, bus, kalshi, poly, venues, store, feed, profit, allocator, risk, execution,
                      engine, matcher, seen)
    yield harness
    await cache.close()
    await repo.close()
