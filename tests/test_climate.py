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


@pytest.mark.parametrize("var", ["temperature", "rainfall"])
async def test_seasonal_change_never_fills_periods_before_the_data(client: httpx.AsyncClient, var: str) -> None:
    """BACKEND_ISSUES #6: p0 "1981–2000" was the year 2000 alone (inclusive window, data start 2000-01)."""
    body = (await client.get(f"{B}/breakdown", params={"metric": "seasonal-change", "var": var})).json()
    c.CategorySet.model_validate(body)
    p = {s["key"]: s for s in body["series"]}
    listed = {u["field"]: u["reason"] for u in body.get("unavailable", [])}
    assert p["p0"]["values"] == [None, None, None, None]
    assert "1981" in listed["series.p0"]
    for key, s in p.items():  # a period is either computed or listed as unavailable, never both
        assert (f"series.{key}" in listed) == all(v is None for v in s["values"]), key


async def test_seasonal_change_var_selects_the_variable(client: httpx.AsyncClient) -> None:
    q = {"metric": "seasonal-change", "municipality": "DC6"}
    t = (await client.get(f"{B}/breakdown", params={**q, "var": "temperature"})).json()
    r = (await client.get(f"{B}/breakdown", params={**q, "var": "rainfall"})).json()
    assert (t["unit"], r["unit"]) == ("°C", "mm")
    assert r["source"] == ["4_3_mean_annual_precipitation_master"] and t["source"] == ["4_1_temperature_change_master"]
    assert all(v is not None for v in r["series"][1]["values"])  # 2001–2020 rainfall: 4_3 covers 2000–2025
    assert [s["values"] for s in t["series"]] != [s["values"] for s in r["series"]]
    bad = await client.get(f"{B}/breakdown", params={"metric": "seasonal-change", "var": "snow"})
    assert bad.status_code == 400 and bad.json()["issues"][0]["allowed"] == ["temperature", "rainfall"]


async def test_drought_frequency_is_area_in_spec_classes(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    """B3: share of the area in D2 / D3 / D4 (spec §3.4, SPI-3 ≤ −1.3), not months of a national-mean SPI ≤ −1.5."""
    body = (await client.get(f"{B}/timeseries", params={"metric": "drought-frequency", "municipality": "DC44"})).json()
    s = {x["key"]: dict(map(tuple, x["data"])) for x in body["series"]}
    assert set(s) == {"d2", "d3", "d4", "trend", "months"}
    assert [a["unit"] for a in body["axes"]] == ["%", "district-months"]
    rows = fixture_db(
        """SELECT round("SPI_3_Short_Term_Agricultural_Drought"::numeric, 2)
           FROM "6.2_rainfall_derived_drought_indices_master" WHERE "District_Name" = 'Alfred Nzo' AND "Year" = 2025"""
    )
    spi = [float(v) for (v,) in rows]
    assert len(spi) == 12
    d2 = sum(1 for v in spi if -1.6 < v <= -1.3)
    d3 = sum(1 for v in spi if -2.0 < v <= -1.6)
    d4 = sum(1 for v in spi if v <= -2.0)
    assert s["d2"]["2025-01-01"] == pytest.approx(d2 / 12 * 100, abs=0.05)
    assert s["d3"]["2025-01-01"] == pytest.approx(d3 / 12 * 100, abs=0.05)
    assert s["d4"]["2025-01-01"] == pytest.approx(d4 / 12 * 100, abs=0.05)
    assert s["months"]["2025-01-01"] == d2 + d3 + d4  # one district: district-months = months
    assert "−1.5" not in body["title"] + body["note"]


async def test_projection_tables_extrapolate_the_observed_trend(client: httpx.AsyncClient) -> None:
    """forecast-summary / risk-outlook: Sen's-slope extrapolation of the annual record (not climate scenarios); an
    indicator with fewer than 8 full years (daily tables start in 2024) is null with the reason."""
    r = await client.get(f"{B}/table", params={"metric": "forecast-summary"})
    assert r.status_code == 200, r.text
    body = r.json()
    c.TableSet.model_validate(body)
    assert [x["id"] for x in body["rows"]] == ["temperature", "hot_days", "rainfall", "et", "streamflow"]
    assert [x["key"] for x in body["columns"]][2:6] == ["baseline", "y2030", "y2050", "y2070"]
    assert "not a climate-model projection" in body["baselineNote"]
    nulls = {x["id"] for x in body["rows"] if x["y2050"] is None}
    assert {"temperature", "streamflow"} <= nulls  # 2024–2025 only in the fixture
    assert {u["field"] for u in body["unavailable"]} >= {f"rows.{i}" for i in nulls}
    ro = await client.get(f"{B}/table", params={"metric": "risk-outlook"})
    assert ro.status_code == 200, ro.text
    levels = {x["level"] for x in ro.json()["rows"]}
    assert levels <= {None, "Low", "Moderate", "High", "Very High"}
