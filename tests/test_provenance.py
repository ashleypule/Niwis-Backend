"""Staleness is measured from the end of a value's period (BACKEND_ISSUES #7).

Newest record in these tests: 2026-09-06 (as in the database), limit 90 days.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.core.provenance import is_stale, latest_complete_period_end, period_end
from tests.conftest import requires_db

D = dt.date
NEWEST = D(2026, 9, 6)
LIMIT = 90


@pytest.mark.parametrize(
    ("as_of", "res", "end"),
    [
        (D(2026, 9, 6), "daily", D(2026, 9, 6)),
        (D(2026, 2, 1), "monthly", D(2026, 2, 28)),
        (D(2024, 2, 1), "monthly", D(2024, 2, 29)),
        (D(2025, 12, 1), "monthly", D(2025, 12, 31)),
        (D(2025, 1, 1), "annual", D(2025, 12, 31)),
        (D(2025, 12, 31), "annual", D(2025, 12, 31)),
    ],
)
def test_period_end(as_of: dt.date, res: str, end: dt.date) -> None:
    assert period_end(as_of, res) == end  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("newest", "res", "end"),
    [
        (D(2026, 9, 6), "daily", D(2026, 9, 6)),
        (D(2026, 9, 6), "monthly", D(2026, 8, 31)),
        (D(2026, 9, 30), "monthly", D(2026, 9, 30)),
        (D(2026, 9, 6), "annual", D(2025, 12, 31)),
        (D(2026, 12, 31), "annual", D(2026, 12, 31)),
        (D(2027, 1, 1), "annual", D(2026, 12, 31)),
    ],
)
def test_latest_complete_period_end(newest: dt.date, res: str, end: dt.date) -> None:
    assert latest_complete_period_end(newest, res) == end  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("as_of", "stale"),
    [
        (D(2026, 9, 6), False),
        (D(2026, 6, 8), False),  # 90 days before the newest record: on the limit
        (D(2026, 6, 7), True),  # 91 days
    ],
)
def test_daily_boundary(as_of: dt.date, stale: bool) -> None:
    assert is_stale(as_of, NEWEST, LIMIT, "daily") is stale


@pytest.mark.parametrize(
    ("as_of", "stale"),
    [
        (D(2026, 8, 1), False),  # latest complete month (ends 2026-08-31)
        (D(2026, 6, 1), False),  # ends 06-30: 68 days
        (D(2026, 5, 1), True),  # ends 05-31: 98 days
        (D(2025, 12, 1), True),  # satellite / hydrology tables: ends 2025-12-31, 249 days
    ],
)
def test_monthly_measures_from_month_end(as_of: dt.date, stale: bool) -> None:
    assert is_stale(as_of, NEWEST, LIMIT, "monthly") is stale


def test_monthly_end_of_month_at_the_limit() -> None:
    # month ending exactly 90 / 91 days before the newest record
    assert is_stale(D(2026, 6, 1), D(2026, 9, 28), LIMIT, "monthly") is False  # 06-30 → 09-28: 90 days
    assert is_stale(D(2026, 6, 1), D(2026, 9, 29), LIMIT, "monthly") is True  # 91 days


@pytest.mark.parametrize(
    ("as_of", "newest", "stale"),
    [
        (D(2025, 1, 1), D(2026, 9, 6), False),  # 2025 is the last complete year (the reported case)
        (D(2025, 12, 31), D(2026, 9, 6), False),  # same year stamped at its end
        (D(2024, 1, 1), D(2026, 9, 6), True),  # a year behind the last complete one
        (D(2025, 1, 1), D(2026, 12, 31), True),  # 2026 is now complete: 2025 ended 365 days ago
        (D(2025, 1, 1), D(2026, 3, 31), False),  # 2025 still the last complete year
        (D(2024, 1, 1), D(2025, 3, 31), False),  # 2024 ended 90 days before: within the limit anyway
        (D(2024, 1, 1), D(2025, 4, 1), False),  # 2024 is the last complete year in April 2025
    ],
)
def test_annual_last_complete_year_is_not_stale(as_of: dt.date, newest: dt.date, stale: bool) -> None:
    assert is_stale(as_of, newest, LIMIT, "annual") is stale


def test_unknown_dates_are_not_stale() -> None:
    assert is_stale(None, NEWEST, LIMIT, "annual") is False
    assert is_stale(D(2020, 1, 1), None, LIMIT, "monthly") is False


# ---------------------------------------------------------------- in the API (fixture DB, newest record 2026-09-06)


@pytest.mark.db
@requires_db
@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/climate/timeseries", {"metric": "extremes"}),
        ("/api/v1/climate/table", {"metric": "statistics"}),
        ("/api/v1/rainfall/breakdown", {"metric": "seasonal"}),
    ],
)
async def test_last_complete_year_is_not_stale(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> None:
    body = (await client.get(path, params={**params, "municipality": "DC6"})).json()
    assert body["asOf"] == "2025-01-01"  # period-start stamping is unchanged
    assert body["periodEnd"] == "2025-12-31"
    assert body["isStale"] is False


@pytest.mark.db
@requires_db
async def test_satellite_months_are_still_stale(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/drought/summary", params={"municipality": "DC6"})).json()
    vci = next(k for k in body["kpis"] if k["id"] == "vci")
    assert (vci["asOf"], vci["periodEnd"], vci["isStale"]) == ("2025-12-01", "2025-12-31", True)
    spi = next(k for k in body["kpis"] if k["id"] == "spi3")
    assert spi["isStale"] is False and spi["periodEnd"] == "2026-08-31"
