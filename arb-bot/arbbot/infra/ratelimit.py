"""Client-side request pacing so the bot stays inside each exchange's rate limits."""
from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Token bucket: `rate` requests per second, bursts up to `burst`."""

    def __init__(self, rate: float, burst: int | None = None):
        self.rate = max(rate, 0.1)
        self.capacity = float(burst or max(int(rate), 1))
        self._tokens = self.capacity
        self._t = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._t) * self.rate)
                self._t = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)

    def penalize(self, seconds: float) -> None:
        """After a 429, drain the bucket so every caller backs off together."""
        self._tokens = min(self._tokens, -seconds * self.rate)
