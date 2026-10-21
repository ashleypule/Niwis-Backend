"""app.core.cache: single-flight loads and refresh-ahead (a value read late in its lifetime is reloaded in the
background while the old one is served, so warmed entries never expire into a cold request)."""

from __future__ import annotations

import asyncio

import pytest

from app.core import cache as cache_mod
from app.core.cache import MemoryCache


async def test_single_flight() -> None:
    c, calls = MemoryCache(), []

    async def load() -> int:
        calls.append(1)
        await asyncio.sleep(0.01)
        return 7

    assert await asyncio.gather(*(c.get_or_set("k", 60, load) for _ in range(5))) == [7] * 5
    assert len(calls) == 1


async def test_refresh_ahead_serves_old_value_and_reloads(monkeypatch: pytest.MonkeyPatch) -> None:
    c = MemoryCache()
    now = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: now[0])
    values = iter([1, 2])

    async def load() -> int:
        return next(values)

    assert await c.get_or_set("k", 100, load) == 1
    now[0] += 50  # half the lifetime: no reload
    assert await c.get_or_set("k", 100, load) == 1
    now[0] += 30  # 80 %: served from cache, reloaded in the background
    assert await c.get_or_set("k", 100, load) == 1
    await asyncio.sleep(0)
    await asyncio.gather(*c._tasks)
    assert await c.get_or_set("k", 100, load) == 2


async def test_failed_refresh_keeps_the_old_value(monkeypatch: pytest.MonkeyPatch) -> None:
    c = MemoryCache()
    now = [0.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: now[0])
    first = True

    async def load() -> int:
        nonlocal first
        if first:
            first = False
            return 1
        raise RuntimeError("statement timeout")

    assert await c.get_or_set("k", 100, load) == 1
    now[0] = 90
    assert await c.get_or_set("k", 100, load) == 1
    await asyncio.gather(*c._tasks)
    assert await c.get_or_set("k", 100, load) == 1  # still cached (TTLCache uses its own clock)
