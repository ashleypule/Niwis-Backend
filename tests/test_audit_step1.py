"""Regression tests for the fixes of the step-1 data audit (docs/AUDIT_STEP1_DATA.md)."""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest

from app.core.stats import rnd
from app.core.thresholds import drought_class

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("value", "nd", "expected"),
    [
        (-1.295, 2, -1.3),  # stored SPI with 3 decimals: the published 2-dp value, not the binary float's
        (1.055, 2, 1.06),
        (-0.495, 2, -0.5),
        (2.5, 0, 3.0),
        (0.1 + 0.2, 2, 0.3),
        (17.84, 1, 17.8),
        (None, 2, None),
        (float("nan"), 2, None),
    ],
)
def test_rnd_rounds_the_decimal_value_half_away_from_zero(value: float | None, nd: int, expected: float | None) -> None:
    assert rnd(value, nd) == expected
    if value is not None and value == value:
        assert rnd(np.float64(value), nd) == expected


@pytest.mark.parametrize(
    ("spi", "cls"),
    [(-1.295, "D2"), (-1.2949, "D1"), (-0.495, "D0"), (-0.795, "D1"), (-1.595, "D3"), (-1.995, "D4"), (-0.4949, "None")],
)
def test_drought_class_uses_the_published_two_decimal_value(spi: float, cls: str) -> None:
    """Spec §3.4 rounds to 2 dp and then compares; −1.295 is published as −1.30 (D2). Python's round / JavaScript's
    Math.round work on the binary value (−1.29499999…) and would say D1."""
    assert drought_class(spi) == cls
    assert drought_class(np.float64(spi)) == cls


async def test_climate_et0_kpi_says_the_year_lacks_31_december(client: httpx.AsyncClient) -> None:
    """The ET₀ source table (meteorological_drought_master) has no 31 December: the annual sum covers 364 days and
    the KPI says so (it is not a full-calendar-year total)."""
    r = await client.get("/api/v1/climate/summary")
    assert r.status_code == 200
    kpi: dict[str, Any] = next(k for k in r.json()["kpis"] if k["id"] == "et0")
    assert "31 Dec" in kpi["sublabel"] and "364 days" in kpi["sublabel"]
