"""Opt-in: the response audit (app.modules.catalog) on the real Supabase database — every endpoint, key and selector
value in four scopes (national, WC, DC32, WMA5) returns 200 / 501 and passes app.core.response_checks against the
real tables' coverage. `pytest -m integration` (several minutes on a cold cache)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.modules import catalog
from scripts.audit_responses import SCOPES, coverage_from

pytestmark = pytest.mark.integration


async def test_every_endpoint_passes_the_response_checks_on_supabase() -> None:
    s = Settings(rate_limit_per_minute=0, response_checks="off", log_level="WARNING")
    if s.supabase_db_url is None:
        pytest.skip("SUPABASE_DB_URL not set")
    app = create_app(s)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test", timeout=300) as c,
    ):
        coverage = coverage_from((await c.get("/api/v1/meta/sources")).json())
        n, problems = await catalog.audit(c, SCOPES, coverage)
    assert n > 500
    assert not problems, "\n".join(problems[:40])
