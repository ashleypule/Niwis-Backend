"""Opt-in, read-only checks against the real Supabase database: `pytest -m integration`.

Uses SUPABASE_DB_URL from .env. Verifies that what /meta reports equals direct SQL: row counts, newest dates, and the
overall newest data date used for staleness.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest

from app.config import Settings
from app.main import create_app
from app.registry.registry import load_registry

pytestmark = pytest.mark.integration
REG = load_registry()


def _url() -> str:
    s = Settings()
    if s.supabase_db_url is None:
        pytest.skip("SUPABASE_DB_URL not set")
    return s.supabase_db_url.get_secret_value()


@pytest.fixture(scope="module")
async def live() -> AsyncIterator[httpx.AsyncClient]:
    _url()
    app = create_app(Settings(rate_limit_per_minute=0, log_level="WARNING"))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as c,
    ):
        yield c


def _sql(query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(_url(), autocommit=True) as c:
        c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        return c.execute(query).fetchall()  # type: ignore[arg-type]


async def test_health_live(live: httpx.AsyncClient) -> None:
    body = (await live.get("/api/v1/health")).json()
    assert body["status"] == "ok" and body["database"]["readOnlySession"] is True


async def test_row_counts_and_as_of_match_db(live: httpx.AsyncClient) -> None:
    items = {i["table"]: i for i in (await live.get("/api/v1/meta/sources")).json()["items"]}
    for table, ds in REG.datasets.items():
        ((n, last),) = _sql(f"SELECT count(*), max({ds.date_sql()}) FROM {ds.fq}")
        assert items[table]["rows"] == n, table
        assert items[table]["asOf"] == last.isoformat(), table


async def test_newest_data_date(live: httpx.AsyncClient) -> None:
    body = (await live.get("/api/v1/meta/sources")).json()
    daily = [ds for ds in REG.datasets.values() if ds.time.resolution == "daily"]
    newest = max(_sql(f"SELECT max({ds.date_sql()}) FROM {ds.fq}")[0][0] for ds in daily)
    assert body["newestDataDate"] == newest.isoformat()
