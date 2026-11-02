"""Part C of audit step 3: the warm-up's shape on the fixture DB, the log noise, and the reads behind the warm-up.

The cold start itself runs on the real database: tests/integration/test_cold_start_live.py.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.logging_config import quiet_model_loggers
from app.main import _warm, page_services
from app.modules import IMPLEMENTED_MODULES
from tests.conftest import requires_db


def test_warm_up_covers_every_live_page_with_a_summary_of_its_own() -> None:
    pages = set(page_services())
    # AI: reads the page forecasts, kept fresh by their own loop; Overview: other modules' panels
    assert IMPLEMENTED_MODULES - {"ai", "overview"} <= pages


def test_prophet_and_cmdstanpy_are_quiet() -> None:
    quiet_model_loggers()
    assert logging.getLogger("cmdstanpy").level == logging.WARNING
    assert logging.getLogger("prophet").level == logging.WARNING
    # "Importing plotly failed" is logged at ERROR by prophet.plot; plotly is not needed
    assert logging.getLogger("prophet.plot").level == logging.CRITICAL


@pytest.mark.db
@requires_db
async def test_warm_up_runs_every_step_and_logs_its_duration(
    client_factory: Any, caplog: pytest.LogCaptureFixture
) -> None:
    async for c in client_factory():
        state = c._transport.app.state.niwis  # type: ignore[attr-defined]
        logger = logging.getLogger("niwis")  # configure_logging replaced the root handlers, caplog's among them
        logger.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.INFO, logger="niwis"):
                results = await _warm(state)
        finally:
            logger.removeHandler(caplog.handler)
        assert not [k for k, v in results.items() if v is None]
        assert {f"{p} summary" for p in page_services()} <= set(results)
        lines = [r for r in caplog.records if r.getMessage() == "cache warm-up step"]
        assert {r.step for r in lines} == set(results)  # type: ignore[attr-defined]
        assert all(r.seconds >= 0 for r in lines)  # type: ignore[attr-defined]
        assert any(r.getMessage() == "cache warm-up finished" for r in caplog.records)


@pytest.mark.db
@requires_db
async def test_daily_stats_are_read_one_district_at_a_time(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grouped read of a daily table must not sort the whole table in one statement (the warm-up's timeout). The
    fixture DB has no (district, date) index; Supabase has one on every daily table."""
    state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    seen: list[str] = []
    real = state.db.fetch

    async def indexed(_: Any, __: Any) -> bool:
        return True

    monkeypatch.setattr(type(state.data), "_indexed_by_location", indexed)

    async def spy(q: str, *args: Any) -> Any:
        seen.append(q)
        return await real(q, *args)

    state.db.fetch = spy
    try:
        await state.cache.clear()
        df = await state.data.daily_monthly_stats("hs_daily_mean_flow", ())
    finally:
        state.db.fetch = real
    assert set(df["district"]) == {"DC6", "CPT", "DC44"}
    stats = [q for q in seen if "daily_mean_flow" in q and "date_trunc" in q]
    assert len(stats) == 52 and all("= ANY($1::text[])" in q for q in stats)
