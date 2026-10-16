"""The newest-date query (app.meta.service.MetaService._newest_sql) returns the same date as a plain max() for every
daily table — with an index skip-scan where a (location, date) index exists, a plain max() otherwise."""

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
        plain = await state.db.fetchval(f"SELECT max({ds.date_sql()}) FROM {ds.fq}")
        if isinstance(fast, str):
            fast = dt.date.fromisoformat(fast)
        assert fast == plain, ds.table
    assert await state.meta.newest_date() == max(
        [await state.db.fetchval(f"SELECT max({ds.date_sql()}) FROM {ds.fq}") for ds in daily]
    )
