"""Centralised in-process event bus for market updates, trading signals,
execution events and risk alerts."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

# Topics
BOOK_UPDATE = "market.book"
MARKETS_DISCOVERED = "market.discovered"
MAPPING_CHANGED = "market.mapping"
OPPORTUNITY = "signal.opportunity"
EXECUTION = "execution.state"
TRADE_COMPLETED = "execution.completed"
PARTIAL_FILL = "execution.partial"
EXECUTION_FAILED = "execution.failed"
EMERGENCY = "execution.emergency"
POSITION_SETTLED = "position.settled"
RISK_ALERT = "risk.alert"
EXCHANGE_STATUS = "exchange.status"
DAILY_SUMMARY = "report.daily"


@dataclass(slots=True)
class Event:
    topic: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self, topic: str, handler: Handler) -> None:
        """topic may end with '*' to match a prefix."""
        self._subs[topic].append(handler)

    def _handlers(self, topic: str) -> list[Handler]:
        out = list(self._subs.get(topic, ()))
        for pat, hs in self._subs.items():
            if pat.endswith("*") and topic.startswith(pat[:-1]):
                out.extend(hs)
        return out

    async def publish(self, topic: str, **data: Any) -> None:
        """Deliver to every subscriber; a failing subscriber never breaks the publisher."""
        ev = Event(topic, data)
        for h in self._handlers(topic):
            try:
                await h(ev)
            except Exception:
                log.exception("event handler failed for %s", topic)

    def publish_nowait(self, topic: str, **data: Any) -> None:
        task = asyncio.get_running_loop().create_task(self.publish(topic, **data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
