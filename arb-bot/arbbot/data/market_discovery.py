"""Autonomous market discovery: pull every open market from both exchanges,
persist them, generate and validate cross-exchange matches, and revalidate
existing mappings."""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from ..core import events
from ..core.events import EventBus
from ..core.market_matcher import MarketMatcher
from ..core.types import Exchange, Market
from ..database.repo import Repository
from ..exchanges.base import ExchangeAdapter

log = logging.getLogger(__name__)


class MarketDiscovery:
    def __init__(self, venues: dict[Exchange, ExchangeAdapter], repo: Repository, matcher: MarketMatcher,
                 bus: EventBus, on_change: Callable[[], Awaitable[None]]):
        self.venues, self.repo, self.matcher, self.bus, self._on_change = venues, repo, matcher, bus, on_change
        self.last_run = 0.0
        self.last_counts: dict = {}

    async def run_once(self) -> dict:
        listed: dict[Exchange, list[Market]] = {}
        for ex, venue in self.venues.items():
            try:
                listed[ex] = await venue.list_markets()
                await self.repo.upsert_markets(listed[ex])
            except Exception as e:
                log.warning("discovery failed on %s: %s", ex.value, e)
                await self.bus.publish(events.EXCHANGE_STATUS, exchange=ex, up=False, error=f"discovery: {e}")
        kalshi, poly = listed.get(Exchange.KALSHI), listed.get(Exchange.POLYMARKET)
        counts = {"kalshi": len(kalshi or []), "polymarket": len(poly or []), "candidates": 0, "verified": 0,
                  "rejected": 0, "demoted": 0}
        if kalshi is not None and poly is not None:
            fresh = {m.key: m for m in kalshi + poly}
            counts["demoted"] = len(await self.matcher.revalidate(fresh))
            enriched: dict[str, Market] = {}

            async def prepare(p: Market) -> Market:
                if p.market_id not in enriched:
                    try:
                        enriched[p.market_id] = await self.venues[Exchange.POLYMARKET].enrich_market(p)
                        await self.repo.upsert_markets([enriched[p.market_id]])
                    except Exception as e:
                        log.warning("enrich failed for %s: %s", p.market_id, e)
                        enriched[p.market_id] = p
                return enriched[p.market_id]

            t0 = time.time()
            counts.update(await self.matcher.run(kalshi, poly, prepare))
            log.info("matching took %.0fs: %s", time.time() - t0, counts)
        self.last_run, self.last_counts = time.time(), counts
        await self._on_change()
        await self.bus.publish(events.MARKETS_DISCOVERED, **counts)
        return counts
