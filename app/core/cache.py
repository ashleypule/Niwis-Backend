"""TTL cache behind a small async interface, so it can be swapped for Redis without touching callers.

`get_or_set` is single-flight per key: concurrent requests for the same uncached key run the loader once. It is also
refresh-ahead: a value read after `REFRESH_AT` of its lifetime is reloaded in the background while the old value keeps
being served, so a periodically touched entry (the warm-up loops, frequent requests) never expires into a cold request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from cachetools import TTLCache

log = logging.getLogger("niwis.cache")
REFRESH_AT = 0.75  # share of the TTL after which a read triggers a background reload


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
        self._stored: dict[str, float] = {}  # key → monotonic time the value was stored
        self._refreshing: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

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
        self._stored[key] = time.monotonic()

    async def get_or_set(self, key: str, ttl_s: int, loader: Callable[[], Awaitable[Any]]) -> Any:
        bucket = self._bucket(ttl_s)
        if key in bucket:
            value = bucket[key]
            age = time.monotonic() - self._stored.get(key, 0.0)
            if age >= REFRESH_AT * ttl_s and key not in self._refreshing:
                self._refreshing.add(key)
                task = asyncio.create_task(self._refresh(key, ttl_s, loader))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            return value
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in bucket:
                return bucket[key]
            value = await loader()
            await self.set(key, value, ttl_s)
            return value

    async def _refresh(self, key: str, ttl_s: int, loader: Callable[[], Awaitable[Any]]) -> None:
        try:
            await self.set(key, await loader(), ttl_s)
        except Exception:  # keep serving the old value until it expires; the next read retries
            log.warning("background cache refresh failed", extra={"key": key[:120]})
        finally:
            self._refreshing.discard(key)

    async def clear(self) -> None:
        for bucket in self._buckets.values():
            bucket.clear()
        self._stored.clear()
