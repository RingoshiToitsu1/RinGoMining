"""Client-side request pacing so the bot stays inside each exchange's rate limits."""
from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Adaptive token bucket. Starts at `rate` requests/second (bursts up to `burst`); every
    429 halves the rate (down to `min_rate`) and every success creeps it back up. The real
    limit of an exchange is often lower than documented, and shared egress IPs make it vary."""

    def __init__(self, rate: float, burst: int | None = None, min_rate: float = 0.2):
        self.max_rate = max(rate, 0.1)
        self.rate = self.max_rate
        self.min_rate = min(min_rate, self.max_rate)
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
        """After a 429: halve the rate and drain the bucket so every caller backs off together."""
        self.rate = max(self.rate / 2, self.min_rate)
        self._tokens = min(self._tokens, -seconds * self.rate)

    def success(self) -> None:
        if self.rate < self.max_rate:
            self.rate = min(self.rate * 1.02 + 0.01, self.max_rate)
