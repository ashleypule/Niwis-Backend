"""Regression tests for the fixes of the step-1 data audit (docs/AUDIT_STEP1_DATA.md)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import httpx
import numpy as np
import pytest

from app.core.spatial import get_spatial
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
    [
        (-1.295, "D2"),
        (-1.2949, "D1"),
        (-0.495, "D0"),
        (-0.795, "D1"),
        (-1.595, "D3"),
        (-1.995, "D4"),
        (-0.4949, "None"),
    ],
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


# ---------------------------------------------------------------- runoff / surface water (audit step 1)


def _series(body: dict[str, Any], key: str) -> list[list[Any]]:
    return next(s for s in body["series"] if s["key"] == key)["data"]


async def test_runoff_coefficient_kpi_is_the_ratio_of_the_window_sums(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    """Σ runoff / Σ rainfall over the window, as the KPI's label says (a scope value is the ratio of the area-weighted
    totals, never the mean of the district ratios; the two differ for a scope)."""
    body = (await client.get("/api/v1/runoff/summary", params={"municipality": "CPT"})).json()
    coef = next(k for k in body["kpis"] if k["id"] == "coefficient")
    ((q,),) = fixture_db(
        """SELECT sum("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000 FROM runoff_variables_master
           WHERE "District_Name" = 'City of Cape Town' AND "Date" BETWEEN '2025-09-07' AND '2026-09-06'"""
    )
    ((p,),) = fixture_db(
        """SELECT sum("Daily_Rainfall_Total_mm") FROM "6.1_core_rainfall_variables_master"
           WHERE "District_Name" = 'City of Cape Town' AND "Date" BETWEEN '2025-09-07' AND '2026-09-06'"""
    )
    assert coef["value"] == round(float(q) / float(p), 3)
    stats = (await client.get("/api/v1/runoff/table", params={"metric": "statistics", "entityId": "CPT"})).json()
    row = next(r for r in stats["rows"] if r["id"] == "coefficient")
    assert row["current"] == coef["value"]  # the KPI and the statistics table agree


async def test_runoff_discharge_monthly_means_cover_complete_months_only(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    """The window starts on 7 Sep 2025 and ends on 6 Sep 2026: the daily line covers those days, but a "monthly mean"
    of six days is not a monthly mean, so the step series holds the complete months Sep 2025 – Aug 2026."""
    body = (await client.get("/api/v1/runoff/timeseries", params={"metric": "discharge", "entityId": "CPT"})).json()
    daily, monthly = _series(body, "daily"), _series(body, "monthly")
    assert daily[0][0] == "2025-09-07" and daily[-1][0] == "2026-09-06"
    assert [p[0] for p in monthly] == [
        f"{y}-{m:02d}-01" for y, m in [(2025, 9), *[(2025, m) for m in (10, 11, 12)], *[(2026, m) for m in range(1, 9)]]
    ]
    area = get_spatial().district_areas()["CPT"]
    ((sep,),) = fixture_db(
        """SELECT avg("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000 FROM runoff_variables_master
           WHERE "District_Name" = 'City of Cape Town' AND "Date" BETWEEN '2025-09-01' AND '2025-09-30'"""
    )
    assert monthly[0][1] == round(float(sep) * area * 1000 / 86400, 2)  # the whole of September, not 7–30 Sep
    assert len(_series(body, "normal")) == len(monthly)


async def test_surface_water_discharge_monthly_means_cover_complete_months_only(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (
        await client.get("/api/v1/surface-water/timeseries", params={"metric": "discharge", "entityId": "CPT"})
    ).json()
    daily, monthly = _series(body, "daily"), _series(body, "monthly")
    first_day = dt.date.fromisoformat(daily[0][0])
    assert first_day.day != 1  # the window starts mid-month (the day after the newest record a year earlier)
    first_month = first_day.replace(day=1)
    assert monthly[0][0] == first_month.isoformat()
    ((mean,),) = fixture_db(
        f"""SELECT avg(daily_mean_flow) / 86400 FROM "9_1_hydrological_station_master"
            WHERE district_name = 'City of Cape Town' AND date_trunc('month', date) = '{first_month}'"""
    )
    assert monthly[0][1] == round(float(mean), 2)  # the whole month
    last_month = dt.date.fromisoformat(monthly[-1][0])
    assert last_month.replace(day=28) + dt.timedelta(days=4) <= dt.date.fromisoformat(daily[-1][0]) + dt.timedelta(
        days=31
    )  # the last monthly point is a complete month at or before the window's end


async def test_runoff_anomaly_page_forecast_history_is_the_runoff_pages_series(
    client: httpx.AsyncClient,
) -> None:
    """The forecast tab's history must be the dashboard's series: the runoff-anomaly target is the Runoff page's own
    anomaly (area-weighted depth vs the 7.2 normal; one district: the stored value), not a mean of district
    percentages (Aug 2026 national: 42.8 % vs the chart's 11.6 % before the fix)."""
    from app.forecasting.pages import VARS
    from app.forecasting.series import district_scope

    state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    target = VARS["runoff-anomaly"].make(state.registry)
    assert target.loader is not None and target.expr_id == "page-anomaly"
    series, how = await target.loader(state, district_scope("CPT"))
    assert "Runoff page" in how
    body = (
        await client.get(
            "/api/v1/runoff/timeseries", params={"metric": "runoff-anomaly", "municipality": "CPT", "period": "5Y"}
        )
    ).json()
    chart = {p[0]: p[1] for p in _series(body, "monthly") if p[1] is not None}
    assert chart, "the fixture serves a runoff anomaly for Cape Town"
    for x, v in chart.items():
        assert round(float(series[x]), 1) == v


# ---------------------------------------------------------------- one extensive-units rule (audit step 1)


def test_one_extensive_units_rule() -> None:
    """Volumes, flows, loads, energy per day and areas are summed over a scope's districts everywhere (the pages,
    the database category charts, the page forecasts); the category view used to average Mm³/year (16_1 mean annual
    runoff), t/day (9_1 sediment load) and kWh/day (13.2 power consumption)."""
    from app.core.units import EXTENSIVE_UNITS
    from app.forecasting import series
    from app.modules import categories, district

    for u in (
        "Mm³",
        "Mm³/month",
        "Mm³/year",
        "m³/s",
        "m³/day",
        "m³/month",
        "m³/year",
        "L/s",
        "km²",
        "t/day",
        "kWh/day",
    ):
        assert u in EXTENSIVE_UNITS
    for u in ("mm", "mm/year", "m bgl", "°C", "%", "mg/L", "m³/m³", "m³/h", "kWh/Ml", "R/year", "L/year"):
        assert u not in EXTENSIVE_UNITS  # states, rates per unit and monetary / per-household quantities
    assert categories.EXTENSIVE_UNITS is EXTENSIVE_UNITS and series.EXTENSIVE_UNITS is EXTENSIVE_UNITS
    assert district.EXTENSIVE_UNITS is EXTENSIVE_UNITS


async def test_surface_water_sediment_load_is_summed_over_the_scope(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/surface-water/timeseries", params={"metric": "sediment", "kind": "load"})).json()
    assert "sum over the scope" in body["note"]
    conc = (await client.get("/api/v1/surface-water/timeseries", params={"metric": "sediment"})).json()
    assert "area-weighted" in conc["note"]  # a concentration stays a mean
