"""Audit step 3: derivations filling gaps from live data (DECISIONS D49) and the backend follow-ups of step 2.

The fixture DB holds three districts and under 36 months of telemetry, so the formulas are tested on synthetic series
and the API paths with a patched frame; the national values are checked against independent SQL on the real database
(`scripts/verify.py audit-step3`).
"""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from app.core.indices import normal_score_index
from app.forecasting.series import interpolate_short_gaps
from app.modules.groundwater.service import SGI_INTERPOLATE, SGI_MAX_MISSING, SGI_MIN_MONTHS, GroundwaterService
from tests.conftest import requires_db


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


# ---------------------------------------------------------------- SGI


def test_normal_score_index_is_the_empirical_normal_per_calendar_month() -> None:
    idx = pd.date_range("2000-01-01", periods=48, freq="MS")
    s = pd.Series(np.arange(48, dtype=float), index=idx)
    z = normal_score_index(s)
    # four Januaries, rising: ranks 1…4 of 4
    jan = z[z.index.month == 1].to_numpy()
    assert jan == pytest.approx(stats.norm.ppf((np.arange(1, 5) - 0.5) / 4))
    # ties share a mid-rank, missing months stay missing and do not count in n
    s2 = pd.Series(
        [1.0, 1.0, 3.0, np.nan], index=pd.to_datetime(["2000-01-01", "2001-01-01", "2002-01-01", "2003-01-01"])
    )
    z2 = normal_score_index(s2)
    assert z2.iloc[0] == z2.iloc[1] == pytest.approx(stats.norm.ppf((1.5 - 0.5) / 3))
    assert np.isnan(z2.iloc[3])


def test_sgi_follows_the_forecast_rule_gap_handling() -> None:
    """Six-weekly sampling leaves 1-month gaps: filled before the 20 % check. A year-long outage stays missing."""
    assert (SGI_MIN_MONTHS, SGI_INTERPOLATE, SGI_MAX_MISSING) == (36, 2, 0.20)
    idx = pd.date_range("2000-01-01", periods=121, freq="MS")
    every_other = pd.Series(np.where(np.arange(121) % 2 == 0, 1.0, np.nan), index=idx)
    assert every_other.isna().mean() > 0.2  # unsampled months exceed 20 % before filling …
    assert interpolate_short_gaps(every_other, SGI_INTERPOLATE).isna().mean() == 0  # … and none remain after it
    outage = pd.Series(np.ones(121), index=idx)
    outage.iloc[30:60] = np.nan
    assert interpolate_short_gaps(outage, SGI_INTERPOLATE).isna().mean() > 0.2


def _frame(codes: list[str], value: float, months: int = 48) -> pd.DataFrame:
    idx = pd.date_range("2022-10-01", periods=months, freq="MS")
    return pd.concat(pd.DataFrame({"district": k, "date": idx, "value": value}) for k in codes)


@pytest.mark.db
@requires_db
async def test_derived_sgi_reaches_the_kpi_map_chart_and_cascade(
    client_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def frame(_: GroundwaterService) -> pd.DataFrame:
        return _frame(["DC6", "CPT", "DC44"], -1.2)

    monkeypatch.setattr(GroundwaterService, "sgi_frame", frame)
    async for own in client_factory():  # its own response cache: the session client must never see the patched frame
        await _check_derived_sgi(own)


async def _check_derived_sgi(client: httpx.AsyncClient) -> None:
    q = {"municipality": "DC6"}
    gw = (await client.get("/api/v1/groundwater/summary", params=q)).json()
    k = kpi(gw, "sgi")
    assert k["value"] == -1.2 and k["derived"] is True and "Bloomfield" in k["methodNote"]
    assert k["status"]["id"] == "Low"
    dr = (await client.get("/api/v1/drought/summary", params=q)).json()
    assert kpi(dr, "sgi")["value"] == -1.2 and "derived" in kpi(dr, "sgi")["sublabel"]
    assert not any(u["field"] == "kpis.sgi" for u in dr.get("unavailable", []))
    cas = (await client.get("/api/v1/drought/table", params={"metric": "cascade", **q})).json()
    row = next(r for r in cas["rows"] if r["id"] == "groundwater")
    assert row["valueNum"] == -1.2 and row["cls"] == "Low" and "derived" in row["stage"]
    m = (await client.get("/api/v1/groundwater/map", params={"layer": "sgi"})).json()
    assert m["derived"] is True and m["values"]["DC6"] == -1.2
    st = (await client.get("/api/v1/groundwater/breakdown", params={"metric": "status", **q})).json()
    assert dict(zip(st["categories"], st["series"][0]["values"], strict=True))["Low"] == 1
    ts = (await client.get("/api/v1/groundwater/timeseries", params={"metric": "sgi", **q})).json()
    assert ts["derived"] is True and ts["axes"][0]["unit"] == "index"


@pytest.mark.db
@requires_db
async def test_sgi_keeps_the_reason_where_the_telemetry_does_not_qualify(client: httpx.AsyncClient) -> None:
    """The fixture holds under 36 months of telemetry: withheld with the rule, and the old column's stale series."""
    k = kpi((await client.get("/api/v1/groundwater/summary", params={"municipality": "DC6"})).json(), "sgi")
    assert k["value"] is None and "at least 36 months" in k["unavailableReason"]


# ---------------------------------------------------------------- land cover


@pytest.mark.db
@requires_db
async def test_land_cover_shares_sum_to_100_and_name_the_table(client: httpx.AsyncClient, fixture_db: Any) -> None:
    b = (
        await client.get(
            "/api/v1/vegetation/breakdown", params={"metric": "land-cover", "kind": "share", "municipality": "DC6"}
        )
    ).json()
    assert b["derived"] is True and b["source"] == ["9_2_catchment_characteristics_master"] and b["unit"] == "%"
    assert sum(b["series"][0]["values"]) == pytest.approx(100, abs=0.5)
    ((tree,),) = fixture_db(
        """SELECT max(tree_cover_pct) FROM "9_2_catchment_characteristics_master"
           WHERE district_name = 'Namakwa' AND year = 2025"""
    )
    assert b["series"][0]["values"][b["categories"].index("Tree cover")] == pytest.approx(float(tree), abs=0.05)
    assert "Cropland" not in b["categories"]  # withheld: a copy of grassland


@pytest.mark.db
@requires_db
async def test_land_cover_ndvi_is_null_for_one_district_and_says_why(client: httpx.AsyncClient) -> None:
    b = (
        await client.get("/api/v1/vegetation/breakdown", params={"metric": "land-cover", "municipality": "DC6"})
    ).json()
    assert all(v is None for v in b["series"][0]["values"])
    assert "one NDVI" in next(u["reason"] for u in b["unavailable"] if u["field"] == "series.a")


@pytest.mark.db
@requires_db
async def test_runoff_serves_the_land_group(client: httpx.AsyncClient) -> None:
    b = (await client.get("/api/v1/runoff/breakdown", params={"metric": "land-cover", "municipality": "DC6"})).json()
    assert b["source"] == ["9_2_catchment_characteristics_master"]
    cats = (await client.get("/api/v1/runoff/categories")).json()
    assert any(t["table"] == "9_2_catchment_characteristics_master" for t in cats["tables"])


# ---------------------------------------------------------------- runoff


@pytest.mark.db
@requires_db
async def test_runoff_peak_flow_uses_the_real_area_never_the_6500_km2_column(
    client: httpx.AsyncClient, fixture_db: Any
) -> None:
    from app.core.spatial import get_spatial

    b = (await client.get("/api/v1/runoff/summary", params={"municipality": "DC6"})).json()
    k = kpi(b, "peak")
    area = (get_spatial().district_areas() or {})["DC6"]
    ((depth,),) = fixture_db(
        f"""SELECT max("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000 FROM runoff_variables_master
            WHERE "District_Name" = 'Namakwa'
            AND "Date" BETWEEN DATE '{k["asOf"]}' - interval '1 year' + interval '1 day' AND DATE '{k["asOf"]}'"""
    )
    assert k["value"] == round(float(depth) * area * 1000 / 86400)


@pytest.mark.db
@requires_db
async def test_runoff_status_by_province_counts_districts(client: httpx.AsyncClient) -> None:
    b = (await client.get("/api/v1/runoff/breakdown", params={"metric": "by-province"})).json()
    assert b["unit"] == "districts" and {s["name"] for s in b["series"]} == {"High Flow", "Normal", "Low Flow"}
    assert sum(sum(s["values"]) for s in b["series"]) == 3  # the fixture's three districts
    m = (await client.get("/api/v1/runoff/map", params={"layer": "flow-status"})).json()
    assert set(m["values"]) == {"DC6", "CPT", "DC44"} and m["derived"] is True


# ---------------------------------------------------------------- surface-water WQI


@pytest.mark.db
@requires_db
async def test_river_wq_status_is_the_water_quality_pages_wqi(client: httpx.AsyncClient) -> None:
    q = {"municipality": "DC44"}
    sw = kpi((await client.get("/api/v1/surface-water/summary", params=q)).json(), "wqi")
    wq = kpi((await client.get("/api/v1/water-quality/summary", params=q)).json(), "wqi")
    assert sw["value"] == wq["value"] and sw["status"] == wq["status"]
    assert sw["derived"] is True and "CCME" in sw["methodNote"] and not sw.get("unit")


def test_wqi_is_null_below_four_parameters() -> None:
    from app.modules.water_quality.service import ccme_wqi

    assert ccme_wqi([("ph", 7.0), ("ec", 50.0), ("do", 8.0)]) is None
    assert ccme_wqi([("ph", 7.0), ("ec", 50.0), ("do", 8.0), ("turbidity", 3.0)]) == 100
