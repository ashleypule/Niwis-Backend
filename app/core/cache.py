"""TTL cache behind a small async interface, so it can be swapped for Redis without touching callers.

`get_or_set` is single-flight per key: concurrent requests for the same uncached key run the loader once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from cachetools import TTLCache


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, ttl_s: int) -> None: ...

    async def get_or_set(self, key: str, ttl_s: int, loader: Callable[[], Awaitable[Any]]) -> Any: ...

    async def clear(self) -> None: ...


class MemoryCache:
    """In-process cache: one cachetools.TTLCache per TTL value."""

    def __init__(self, maxsize: int = 2048) -> None:
        self._maxsize = maxsize
        self._buckets: dict[int, TTLCache[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _bucket(self, ttl_s: int) -> TTLCache[str, Any]:
        if ttl_s not in self._buckets:
            self._buckets[ttl_s] = TTLCache(maxsize=self._maxsize, ttl=ttl_s)
        return self._buckets[ttl_s]

    async def get(self, key: str) -> Any | None:
        for bucket in self._buckets.values():
            if key in bucket:
                return bucket[key]
        return None

    async def set(self, key: str, value: Any, ttl_s: int) -> None:
        self._bucket(ttl_s)[key] = value

    async def get_or_set(self, key: str, ttl_s: int, loader: Callable[[], Awaitable[Any]]) -> Any:
        bucket = self._bucket(ttl_s)
        if key in bucket:
            return bucket[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in bucket:
                return bucket[key]
            value = await loader()
            bucket[key] = value
            return value

    async def clear(self) -> None:
        for bucket in self._buckets.values():
            bucket.clear()
