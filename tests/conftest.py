"""Shared test fixtures.

API tests run against a local Postgres loaded with the recorded fixture (`docker compose --profile test up -d testdb`,
URL in NIWIS_TEST_DB_URL). Tests marked `db` are skipped when it is not running. Nothing here touches Supabase;
tests marked `integration` do, read-only, and only when run with `pytest -m integration`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import psycopg
import pytest

from app.config import Settings
from app.main import create_app

TEST_DB_URL = os.environ.get("NIWIS_TEST_DB_URL", "postgresql://postgres:test@localhost:55432/niwis_test")


def _db_up() -> bool:
    try:
        with psycopg.connect(TEST_DB_URL, connect_timeout=2) as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


DB_UP = _db_up()
requires_db = pytest.mark.skipif(not DB_UP, reason="fixture DB not running: docker compose --profile test up -d testdb")


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "supabase_db_url": TEST_DB_URL,
        "rate_limit_per_minute": 0,
        "log_level": "WARNING",
        "environment": "test",
        "response_checks": "raise",  # every response in the test suite must pass app.core.response_checks
        "prewarm_categories": False,  # tested explicitly (tests/test_categories.py); keeps the pool free for tests
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture(scope="session")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """App wired to the fixture DB (session-scoped: the meta cache is shared across tests)."""
    if not DB_UP:
        pytest.skip("fixture DB not running")
    async for c in _client(make_settings()):
        yield c


@pytest.fixture
def client_factory() -> Callable[..., AsyncIterator[httpx.AsyncClient]]:
    """Build a client with custom settings: `async for c in client_factory(rate_limit_per_minute=2): ...`."""

    def factory(**overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
        return _client(make_settings(**overrides))

    return factory


@pytest.fixture(scope="session")
def fixture_db() -> Callable[[str], list[tuple[Any, ...]]]:
    """Run a read-only query against the fixture DB (for comparing API values with SQL)."""

    def run(sql: str) -> list[tuple[Any, ...]]:
        with psycopg.connect(TEST_DB_URL, autocommit=True) as c:
            c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            return c.execute(sql).fetchall()  # type: ignore[arg-type]

    return run
