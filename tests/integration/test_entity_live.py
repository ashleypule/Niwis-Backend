"""Opt-in: `entityId` (B1) on the real Supabase database — every entity dataset, two districts, four scopes.
`pytest -m integration`."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.entity_audit import check

pytestmark = pytest.mark.integration


async def test_entity_datasets_follow_the_district_on_supabase() -> None:
    s = Settings(rate_limit_per_minute=0, log_level="WARNING")
    if s.supabase_db_url is None:
        pytest.skip("SUPABASE_DB_URL not set")
    app = create_app(s)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=300) as c,
    ):
        problems, compared = await check(c, "DC44", "DC32", [{}, {"province": "WC"}, {"wma": "WMA5"}])
    assert not problems, "\n".join(problems)
    assert compared >= 11, compared  # every entity dataset has data for both districts on Supabase
