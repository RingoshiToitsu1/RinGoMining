"""Live order-book ingestion: an in-memory store (mirrored to Redis with a
short TTL) fed by REST polling and, where available, WebSocket streams."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from typing import Callable

from ..core import events
from ..core.events import EventBus
from ..core.types import Exchange, Market, OrderBook, Outcome
from ..exchanges.base import ExchangeAdapter
from ..infra.cache import Cache

log = logging.getLogger(__name__)


class OrderBookStore:
    def __init__(self, cache: Cache | None = None, history_s: float = 120.0):
        self._books: dict[str, OrderBook] = {}
        self._mids: dict[str, deque[tuple[float, float]]] = {}
        self._cache = cache
        self._history_s = history_s

    @staticmethod
    def key(exchange: Exchange, market_id: str) -> str:
        return f"{exchange.value}:{market_id}"

    def put(self, book: OrderBook) -> None:
        k = self.key(book.exchange, book.market_id)
        self._books[k] = book
        mid = book.mid(Outcome.YES)
        if mid is not None:
            dq = self._mids.setdefault(k, deque())
            dq.append((book.ts, float(mid)))
            while dq and dq[0][0] < book.ts - self._history_s:
                dq.popleft()

    async def put_async(self, book: OrderBook) -> None:
        self.put(book)
        if self._cache:
            try:
                await self._cache.set("book:" + self.key(book.exchange, book.market_id), book.to_dict(), ttl=30)
            except Exception:
                log.debug("cache write failed", exc_info=True)

    def get(self, exchange: Exchange, market_id: str) -> OrderBook | None:
        return self._books.get(self.key(exchange, market_id))

    def vol_per_s(self, exchange: Exchange, market_id: str) -> float:
        """Relative mid-price volatility per sqrt-second over the recent window.

        Robust (median-based) on purpose: a one-off jump on one venue is the dislocation
        being traded, not evidence that quotes will keep moving during the order latency.
        """
        dq = self._mids.get(self.key(exchange, market_id))
        if not dq or len(dq) < 4:
            return 0.0
        pts = list(dq)
        rates = []
        for (t0, m0), (t1, m1) in zip(pts, pts[1:]):
            dt = t1 - t0
            if m0 > 0 and dt > 0:
                rates.append(abs(m1 - m0) / m0 / math.sqrt(dt))
        if not rates:
            return 0.0
        rates.sort()
        return 1.4826 * rates[len(rates) // 2]

    def last_update(self, exchange: Exchange) -> float:
        ts = [b.ts for k, b in self._books.items() if k.startswith(exchange.value + ":")]
        return max(ts) if ts else 0.0


class BookFeed:
    """Keeps the store fresh for every market in the watch list."""

    def __init__(self, adapters: dict[Exchange, ExchangeAdapter], store: OrderBookStore, bus: EventBus,
                 interval: Callable[[], float], recorder: Callable[[OrderBook], None] | None = None):
        self.adapters, self.store, self.bus = adapters, store, bus
        self._interval = interval
        self._watch: dict[Exchange, dict[str, Market]] = {e: {} for e in Exchange}
        self._stream_tasks: dict[Exchange, asyncio.Task] = {}
        self._stream_sig: dict[Exchange, frozenset[str]] = {}
        self._recorder = recorder
        self.last_ok: dict[Exchange, float] = {e: 0.0 for e in Exchange}
        self.errors: dict[Exchange, int] = {e: 0 for e in Exchange}
        self._up: dict[Exchange, bool] = {e: True for e in Exchange}

    def watch(self, markets: list[Market]) -> None:
        new: dict[Exchange, dict[str, Market]] = {e: {} for e in Exchange}
        for m in markets:
            if m.exchange in new:
                new[m.exchange][m.market_id] = m
        self._watch = new

    async def _on_book(self, book: OrderBook) -> None:
        await self.store.put_async(book)
        self.last_ok[book.exchange] = time.time()
        if self._recorder:
            self._recorder(book)
        await self.bus.publish(events.BOOK_UPDATE, exchange=book.exchange, market_id=book.market_id)

    async def poll_once(self, exchange: Exchange) -> int:
        markets = list(self._watch[exchange].values())
        if not markets:
            self.last_ok[exchange] = time.time()
            return 0
        adapter = self.adapters[exchange]
        try:
            books = await adapter.get_orderbooks(markets)
        except Exception as e:
            self.errors[exchange] += 1
            if self._up[exchange] and self.errors[exchange] >= 3:
                self._up[exchange] = False
                await self.bus.publish(events.EXCHANGE_STATUS, exchange=exchange, up=False, error=str(e))
            raise
        for b in books.values():
            await self._on_book(b)
        self.errors[exchange] = 0
        if not self._up[exchange]:
            self._up[exchange] = True
            await self.bus.publish(events.EXCHANGE_STATUS, exchange=exchange, up=True, error="")
        return len(books)

    def _ensure_stream(self, exchange: Exchange) -> None:
        adapter = self.adapters[exchange]
        if not adapter.supports_streaming:
            return
        sig = frozenset(self._watch[exchange])
        task = self._stream_tasks.get(exchange)
        if task and not task.done() and sig == self._stream_sig.get(exchange):
            return
        if task:
            task.cancel()
        if not sig:
            return
        self._stream_sig[exchange] = sig

        async def run() -> None:
            backoff = 1.0
            while True:
                try:
                    await adapter.stream_orderbooks(list(self._watch[exchange].values()), self._on_book)
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except NotImplementedError:
                    return
                except Exception as e:
                    log.warning("%s stream dropped: %s", exchange.value, e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

        self._stream_tasks[exchange] = asyncio.create_task(run(), name=f"stream-{exchange.value}")

    async def run(self) -> None:
        """REST polling is always on: it is the safety net under the streams."""
        while True:
            for ex in list(self.adapters):
                self._ensure_stream(ex)
                try:
                    await self.poll_once(ex)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("book poll failed on %s: %s", ex.value, e)
            await asyncio.sleep(self._interval())

    async def stop(self) -> None:
        for t in self._stream_tasks.values():
            t.cancel()
