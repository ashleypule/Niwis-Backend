"""Climate endpoints on the recorded fixture DB (DC44, CPT, DC6; daily tables from 2024, annual tables complete)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/climate"


def kpis(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["id"]: k for k in body["kpis"]}


async def test_summary(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    c.KpiSet.model_validate(body)
    k = kpis(body)
    ((r20,),) = fixture_db(
        """SELECT "R20mm_Days" FROM "4_2_wet_dry_spells_master" WHERE "District_Name" = 'Namakwa' AND "Year" = 2025"""
    )
    assert k["r20"]["value"] == float(r20)
    assert k["tempTrend"]["value"] is None  # fixture daily temperatures cover < 8 full years: no trend, not a guess
    assert k["tempAnomaly"]["asOf"] == "2026-08-01"
    assert body["baseline"] == "2000–2026 monthly mean"


async def test_rain_change_matches_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "CPT"})).json()
    ((v,),) = fixture_db(
        """WITH a AS (SELECT "Year" y, "MAP_mm" p FROM "4_3_mean_annual_precipitation_master"
                      WHERE "District_Name" = 'City of Cape Town' AND "Year" BETWEEN 2000 AND 2025)
           SELECT round((((SELECT p FROM a WHERE y = 2025) - avg(p)) / avg(p) * 100)::numeric, 1) FROM a"""
    )
    assert kpis(body)["rainChange"]["value"] == pytest.approx(float(v), abs=0.05)


async def test_hot_days_map_is_count_over_35(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get(f"{B}/map", params={"layer": "hot-days"})).json()
    ((n,),) = fixture_db(
        """SELECT count(*) FROM "4_1_temperature_change_master"
           WHERE "District_Name" = 'Namakwa' AND "Tmax_C" > 35 AND "Date" LIKE '2025-%'"""
    )
    assert body["values"]["DC6"] == n


@pytest.mark.parametrize(
    "key",
    [
        "temperature-long",
        "national-trend",
        "extremes",
        "heat-days",
        "extreme-rain",
        "evaporation",
        "streamflow-linkage",
        "drought-frequency",
        "indicator-trends",
    ],
)
async def test_timeseries_validate(client: httpx.AsyncClient, key: str) -> None:
    r = await client.get(f"{B}/timeseries", params={"metric": key, "municipality": "CPT"})
    assert r.status_code == 200, r.text
    c.SeriesSet.model_validate(r.json())


async def test_evaporation_withheld_series(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/timeseries", params={"metric": "evaporation", "municipality": "DC44"})).json()
    s = {x["key"]: x for x in body["series"]}
    assert all(p[1] is None for p in s["pan"]["data"]) and all(p[1] is None for p in s["vpd"]["data"])
    assert {u["field"] for u in body["unavailable"]} == {"series.pan", "series.vpd"}
    assert "4_4_potential_evaporation_daily_master" not in body["source"]


@pytest.mark.parametrize(
    ("kind", "key", "model"),
    [
        ("breakdown", "seasonal-change", c.CategorySet),
        ("breakdown", "map-seasonality", c.CategorySet),
        ("table", "statistics", c.TableSet),
        ("table", "signal-chain", c.TableSet),
    ],
)
async def test_other_datasets(client: httpx.AsyncClient, kind: str, key: str, model: type[c.ContractModel]) -> None:
    r = await client.get(f"{B}/{kind}", params={"metric": key, "municipality": "DC6"})
    assert r.status_code == 200, r.text
    model.model_validate(r.json())


async def test_seasonal_change_pre_2000_unavailable(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/breakdown", params={"metric": "seasonal-change", "municipality": "DC6"})).json()
    first = body["series"][0]
    assert first["name"] == "1981–2000" and all(v is None for v in first["values"])


@pytest.mark.parametrize("layer", ["temp-trend", "temp-anomaly", "mean-temp", "rain-trend", "hot-days", "cdd", "et0"])
async def test_maps(client: httpx.AsyncClient, layer: str) -> None:
    body = (await client.get(f"{B}/map", params={"layer": layer, "province": "EC"})).json()
    c.MapLayer.model_validate(body)
    assert set(body["values"]) == {"DC44"}


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("timeseries", {"metric": "projections"}),
        ("timeseries", {"metric": "irrigation"}),
        ("breakdown", {"metric": "risk-index"}),
        ("breakdown", {"metric": "projection-bars"}),
        ("table", {"metric": "forecast-summary"}),
        ("table", {"metric": "risk-outlook"}),
        ("map", {"layer": "vulnerability"}),
        ("map", {"layer": "proj-availability"}),
    ],
)
async def test_unavailable(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> None:
    r = await client.get(f"{B}/{path}", params=params)
    assert r.status_code == 501 and r.json()["code"] == "DATA_UNAVAILABLE"


async def test_forecast_has_no_keys(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{B}/forecast", params={"metric": "anything"})).status_code == 404


async def test_no_withheld_tables_in_any_response(client: httpx.AsyncClient) -> None:
    withheld = {
        "4_4_potential_evaporation_daily_master",
        "4_5_irrigation_demand_daily_master",
        "4_6_streamflow_and_climate_linkage_annual _master",
    }
    for metric in ("evaporation", "streamflow-linkage"):
        body = (await client.get(f"{B}/timeseries", params={"metric": metric, "municipality": "DC6"})).json()
        assert not withheld & set(body["source"])
