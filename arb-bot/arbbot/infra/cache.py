"""Short-lived market data cache and distributed locks.

Redis in production; an in-process fallback keeps tests and single-node paper
trading dependency-free. Nothing stored here is a trading record.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Protocol


class LockNotAcquired(Exception):
    pass


class Cache(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, ttl: float) -> None: ...
    def lock(self, name: str, ttl: float = 60.0, wait: float = 0.0) -> Any: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, float] = {}

    async def get(self, key: str) -> Any:
        hit = self._data.get(key)
        if not hit or hit[0] < time.monotonic():
            self._data.pop(key, None)
            return None
        return hit[1]

    async def set(self, key: str, value: Any, ttl: float) -> None:
        self._data[key] = (time.monotonic() + ttl, value)

    @asynccontextmanager
    async def lock(self, name: str, ttl: float = 60.0, wait: float = 0.0) -> AsyncIterator[None]:
        deadline = time.monotonic() + wait
        while self._locks.get(name, 0) > time.monotonic():
            if time.monotonic() >= deadline:
                raise LockNotAcquired(name)
            await asyncio.sleep(0.02)
        self._locks[name] = time.monotonic() + ttl
        try:
            yield
        finally:
            self._locks.pop(name, None)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass


_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class RedisCache:
    def __init__(self, url: str, prefix: str = "arbbot:"):
        import redis.asyncio as redis

        self._r = redis.from_url(url, decode_responses=True)
        self._prefix = prefix

    async def get(self, key: str) -> Any:
        raw = await self._r.get(self._prefix + key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: Any, ttl: float) -> None:
        await self._r.set(self._prefix + key, json.dumps(value, default=str), px=max(int(ttl * 1000), 1))

    @asynccontextmanager
    async def lock(self, name: str, ttl: float = 60.0, wait: float = 0.0) -> AsyncIterator[None]:
        key, token = f"{self._prefix}lock:{name}", uuid.uuid4().hex
        deadline = time.monotonic() + wait
        while not await self._r.set(key, token, nx=True, px=int(ttl * 1000)):
            if time.monotonic() >= deadline:
                raise LockNotAcquired(name)
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            await self._r.eval(_RELEASE, 1, key, token)

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._r.aclose()


def make_cache(redis_url: str) -> Cache:
    return RedisCache(redis_url) if redis_url else MemoryCache()
