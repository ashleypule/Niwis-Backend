"""The newest-date query (app.meta.service.MetaService._newest_sql) returns the same date as a plain max() for every
daily table — with an index skip-scan where a (location, date) index exists, a plain max() otherwise — never counting
rows dated after today (8_1 and 10_1 hold rows up to 2026-12-31)."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


async def test_newest_sql_matches_plain_max(client: httpx.AsyncClient) -> None:
    state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    daily = [ds for ds in state.registry.datasets.values() if ds.time.resolution == "daily"]
    assert daily
    for ds in daily:
        fast = await state.db.fetchval(await state.meta._newest_sql(ds))
        plain = await state.db.fetchval(f"SELECT max({ds.date_sql()}) FROM {ds.fq} WHERE {ds.not_future_sql()}")
        if isinstance(fast, str):
            fast = dt.date.fromisoformat(fast)
        assert fast == plain, ds.table
    assert await state.meta.newest_date() == max(
        [
            await state.db.fetchval(f"SELECT max({ds.date_sql()}) FROM {ds.fq} WHERE {ds.not_future_sql()}")
            for ds in daily
        ]
    )


async def test_rows_after_today_are_never_newest(client: httpx.AsyncClient) -> None:
    state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    ds = state.registry.datasets["8_1_reservoir_operational_master"]
    assert (
        await state.db.fetchval(f"SELECT max({ds.date_sql()}) FROM {ds.fq}") > dt.date.today()
    )  # the fixture has them
    newest = await state.meta.newest_date()
    assert newest is not None and newest <= dt.date.today()
