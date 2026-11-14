"""Simulated exchange used for paper trading, backtesting and tests.

PaperExchange wraps a real adapter for *market data only* and fills orders
against the live book with simulated latency, failures and partial fills. It
holds no reference to any order-placing method of the real adapter, so paper
trading cannot place a real order.
"""
from __future__ import annotations

import asyncio
import random
from collections import deque
from decimal import Decimal
from typing import Any, Awaitable, Callable

from ..core.fees import estimate_fee
from ..core.types import (ONE, ZERO, Balance, D, EligibilityReport, Exchange, Level, Market, OrderBook,
                          OrderRequest, OrderResult, OrderStatus, Outcome, Resolution, Side, TIF, floor_to)
from .base import BookCallback, ExchangeAdapter, ExchangeUnavailable


class SimExchange(ExchangeAdapter):
    is_simulated = True

    def __init__(self, name: Exchange, balance: Decimal = Decimal("1000"), *, latency_s: float = 0.0,
                 fail_prob: float = 0.0, partial_prob: float = 0.0, seed: int | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.name = name
        self.cash = D(balance)
        self.latency_s, self.fail_prob, self.partial_prob = latency_s, fail_prob, partial_prob
        self.rng = random.Random(seed)
        self._sleep = sleep
        self.markets: dict[str, Market] = {}
        self.books: dict[str, OrderBook] = {}
        self.positions: dict[tuple[str, Outcome], Decimal] = {}
        self.resolutions: dict[str, tuple[Resolution, Decimal]] = {}
        self.orders: dict[str, OrderResult] = {}          # by order id
        self._by_client: dict[str, str] = {}
        self._seq = 0
        self.healthy = True
        # test hooks: each entry applies to the next order.
        #   Decimal ratio -> fill at most that share of the order
        #   "reject" | "raise_before" (nothing happens) | "raise_after" (order fills, ack lost)
        self.forced: deque[Any] = deque()
        self.order_log: list[OrderRequest] = []

    # -- setup helpers -------------------------------------------------------
    def add_market(self, market: Market, book: OrderBook | None = None) -> None:
        self.markets[market.market_id] = market
        if book:
            self.books[market.market_id] = book

    def set_book(self, market_id: str, *, yes_bids: list[tuple] = (), yes_asks: list[tuple] = (),
                 no_bids: list[tuple] | None = None, no_asks: list[tuple] | None = None,
                 ts: float | None = None) -> OrderBook:
        def lv(xs: Any) -> list[Level]:
            return [Level(D(p), D(s)) for p, s in xs]

        if no_bids is None and no_asks is None:
            book = OrderBook.from_single_book(self.name, market_id, lv(yes_bids), lv(yes_asks))
        else:
            book = OrderBook(self.name, market_id, lv(yes_bids), lv(yes_asks), lv(no_bids or ()),
                             lv(no_asks or ())).normalize()
        if ts is not None:
            book.ts = ts
        self.books[market_id] = book
        return book

    # -- adapter interface -----------------------------------------------------
    async def check_eligibility(self) -> EligibilityReport:
        rep = EligibilityReport(self.name)
        rep.add("simulated venue", True, "no real orders are placed")
        return rep

    async def health(self) -> bool:
        return self.healthy

    async def list_markets(self) -> list[Market]:
        return list(self.markets.values())

    async def get_market(self, market_id: str) -> Market | None:
        return self.markets.get(market_id)

    async def _book(self, market: Market) -> OrderBook:
        if not self.healthy:
            raise ExchangeUnavailable(f"{self.name.value} simulated outage")
        book = self.books.get(market.market_id)
        if book is None:
            raise ExchangeUnavailable(f"no book for {market.market_id}")
        return book

    async def get_orderbook(self, market: Market) -> OrderBook:
        import copy
        import time

        book = copy.deepcopy(await self._book(market))
        if not getattr(self, "replay_clock", None):
            book.ts = time.time()
        return book

    async def get_resolution(self, market: Market) -> tuple[Resolution, Decimal] | None:
        return self.resolutions.get(market.market_id)

    async def get_balance(self) -> Balance:
        if not self.healthy:
            raise ExchangeUnavailable(f"{self.name.value} simulated outage")
        return Balance(self.cash, self.cash)

    async def get_positions(self) -> dict[tuple[str, Outcome], Decimal]:
        return {k: v for k, v in self.positions.items() if v != 0}

    async def get_open_orders(self) -> list[dict]:
        return []

    async def get_order(self, order_id: str, market: Market) -> OrderResult:
        return self.orders[order_id]

    async def cancel_order(self, order_id: str, market: Market) -> None:
        return None

    def _consume(self, market_id: str, outcome: Outcome, side: Side, fills: list[tuple[Decimal, Decimal]]) -> None:
        """Remove filled liquidity from a locally held book (both views of it)."""
        book = self.books.get(market_id)
        if book is None:
            return
        for price, size in fills:
            views = [(book.asks(outcome) if side is Side.BUY else book.bids(outcome), price),
                     (book.bids(outcome.opposite) if side is Side.BUY else book.asks(outcome.opposite), ONE - price)]
            for levels, px in views:
                for i, lv in enumerate(levels):
                    if lv.price == px:
                        left = lv.size - size
                        if left > 0:
                            levels[i] = Level(px, left)
                        else:
                            levels.pop(i)
                        break

    async def place_order(self, req: OrderRequest) -> OrderResult:
        if req.client_order_id in self._by_client:       # idempotent replay
            return self.orders[self._by_client[req.client_order_id]]
        self.order_log.append(req)
        forced = self.forced.popleft() if self.forced else None
        if forced == "raise_before":
            raise ExchangeUnavailable("simulated timeout before submission")
        if self.latency_s:
            await self._sleep(self.latency_s)
        self._seq += 1
        oid = f"{self.name.value}-sim-{self._seq}"

        def done(res: OrderResult) -> OrderResult:
            res.order_id = oid
            self.orders[oid] = res
            self._by_client[req.client_order_id] = oid
            return res

        if forced == "reject" or (forced is None and self.rng.random() < self.fail_prob):
            return done(OrderResult(OrderStatus.REJECTED, message="simulated rejection"))

        book = await self._book(req.market)
        if req.side is Side.BUY:
            levels = [lv for lv in book.asks(req.outcome) if lv.price <= req.limit_price]
        else:
            levels = [lv for lv in book.bids(req.outcome) if lv.price >= req.limit_price]
            held = self.positions.get((req.market.market_id, req.outcome), ZERO)
            if held < req.size:
                return done(OrderResult(OrderStatus.REJECTED, message="insufficient position"))
        available = sum((lv.size for lv in levels), ZERO)
        target = min(req.size, available)
        if isinstance(forced, Decimal):
            target = min(target, req.size * forced)
        elif forced is None and self.rng.random() < self.partial_prob:
            target = target * Decimal(str(round(self.rng.uniform(0.3, 0.9), 2)))
        target = floor_to(target, req.market.size_step)
        if target <= 0 or (req.tif is TIF.FOK and target < req.size):
            return done(OrderResult(OrderStatus.UNFILLED, message="no fill at limit"))

        fills, remaining, notional = [], target, ZERO
        for lv in levels:
            if remaining <= 0:
                break
            take = min(lv.size, remaining)
            fills.append((lv.price, take))
            notional += take * lv.price
            remaining -= take
        fee = sum((estimate_fee(req.market.fee, p, s) for p, s in fills), ZERO)
        key = (req.market.market_id, req.outcome)
        if req.side is Side.BUY:
            if notional + fee > self.cash:
                return done(OrderResult(OrderStatus.REJECTED, message="insufficient balance"))
            self.cash -= notional + fee
            self.positions[key] = self.positions.get(key, ZERO) + target
        else:
            self.cash += notional - fee
            self.positions[key] = self.positions.get(key, ZERO) - target
        self._consume(req.market.market_id, req.outcome, req.side, fills)
        status = OrderStatus.FILLED if target == req.size else OrderStatus.PARTIAL
        res = done(OrderResult(status, filled_size=target, avg_price=notional / target, fee=fee))
        if forced == "raise_after":
            raise ExchangeUnavailable("simulated timeout after submission")
        return res

    async def on_position_settled(self, market: Market, outcome: Outcome, size: Decimal, payout: Decimal) -> None:
        key = (market.market_id, outcome)
        self.positions[key] = self.positions.get(key, ZERO) - size
        self.cash += payout

    # -- persistence (paper accounts survive restarts) -------------------------
    def state(self) -> dict:
        return {"cash": str(self.cash), "seq": self._seq,
                "positions": [[m, o.value, str(v)] for (m, o), v in self.positions.items() if v != 0]}

    def load_state(self, st: dict | None) -> None:
        if not st:
            return
        self.cash, self._seq = D(st["cash"]), int(st.get("seq", 0))
        self.positions = {(m, Outcome(o)): D(v) for m, o, v in st.get("positions", [])}


class PaperExchange(SimExchange):
    """Simulated account + real market data."""

    def __init__(self, data: ExchangeAdapter, balance: Decimal, **kw: Any):
        super().__init__(data.name, balance, **kw)
        self._data_list_markets = data.list_markets
        self._data_get_market = data.get_market
        self._data_get_orderbook = data.get_orderbook
        self._data_get_orderbooks = data.get_orderbooks
        self._data_get_resolution = data.get_resolution
        self._data_health = data.health
        self._data_stream = data.stream_orderbooks
        self._data_enrich = data.enrich_market
        self._data_quotes = data.get_quotes
        self.supports_streaming = data.supports_streaming
        self.conversion_bps = data.conversion_bps

    async def health(self) -> bool:
        return await self._data_health()

    async def list_markets(self) -> list[Market]:
        return await self._data_list_markets()

    async def get_market(self, market_id: str) -> Market | None:
        return await self._data_get_market(market_id)

    async def enrich_market(self, market: Market) -> Market:
        return await self._data_enrich(market)

    async def get_quotes(self, markets: list[Market]) -> dict:
        return await self._data_quotes(markets)

    async def _book(self, market: Market) -> OrderBook:
        return await self._data_get_orderbook(market)

    async def get_orderbook(self, market: Market) -> OrderBook:
        return await self._data_get_orderbook(market)

    async def get_orderbooks(self, markets: list[Market]) -> dict[str, OrderBook]:
        return await self._data_get_orderbooks(markets)

    async def stream_orderbooks(self, markets: list[Market], on_book: BookCallback) -> None:
        await self._data_stream(markets, on_book)

    async def get_resolution(self, market: Market) -> tuple[Resolution, Decimal] | None:
        return await self._data_get_resolution(market)

    def _consume(self, *a: Any, **k: Any) -> None:
        return None  # the real book is not ours to mutate
