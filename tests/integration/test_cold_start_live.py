"""Cold start on the real database (read-only): the first warm-up round finishes with no failed step (audit step 3).

The 9_1 surface-water read used to pass the statement timeout on a cold database, so the first visitor waited for it.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.main import _warm, create_app

pytestmark = pytest.mark.integration


async def test_cold_start_warm_up_has_no_timeout() -> None:
    app = create_app(Settings(prewarm_categories=False, forecast_pages=False, rate_limit_per_minute=0))
    async with app.router.lifespan_context(app):
        state = app.state.niwis
        await state.cache.clear()  # the lifespan's own round may have begun: start from nothing
        results = await _warm(state)
    failed = [k for k, v in results.items() if v is None]
    assert not failed, failed
    assert results["surface water"] is not None
