"""Rainfall endpoints on the recorded fixture DB (districts Alfred Nzo DC44, City of Cape Town CPT, Namakwa DC6).

The fixture holds 3 of 52 districts, so national / provincial aggregates fall below the 95 % area-coverage rule and
must come back as null — which is itself the behaviour under test. District-scoped values are checked against SQL.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/rainfall"


def kpis(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["id"]: k for k in body["kpis"]}


async def test_summary_district_matches_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    r = await client.get(f"{B}/summary", params={"municipality": "CPT"})
    assert r.status_code == 200
    body = r.json()
    c.KpiSet.model_validate(body)
    k = kpis(body)
    ((total,),) = fixture_db(
        """SELECT round(sum("Daily_Rainfall_Total_mm")::numeric, 1) FROM "6.1_core_rainfall_variables_master"
           WHERE "District_Name" = 'City of Cape Town' AND "Date"::date BETWEEN '2025-09-07' AND '2026-09-06'"""
    )
    assert k["total"]["value"] == float(total)
    assert body["scope"] == {"level": "municipality", "id": "CPT", "name": "City of Cape Town"}
    assert body["aggregation"] == "district"
    assert body["asOf"] == "2026-09-06"
    assert k["spi6"]["asOf"] == "2026-08-01"  # latest complete month, not the partial September
    assert k["spi6"]["status"]["scale"] == "drought"
    assert body["baseline"] == "2000–2026 monthly mean" and "WMO" in body["baselineNote"]
    assert {comp["key"] for comp in body["components"]} >= {"total", "spi6", "spei6"}


async def test_summary_stations_unavailable(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC44"})).json()
    st = kpis(body)["stations"]
    assert st["value"] is None and st["unavailableReason"]
    assert {"field": "kpis.stations", "reason": st["unavailableReason"]} in body["unavailable"]


async def test_national_needs_area_coverage(client: httpx.AsyncClient) -> None:
    """3 of 52 districts cover < 95 % of the area: aggregates must be null, never a mean of the 3."""
    body = (await client.get(f"{B}/summary")).json()
    k = kpis(body)
    assert k["total"]["value"] is None and k["spi6"]["value"] is None
    assert body["aggregation"] == "district-area-weighted"


async def test_partial_month_not_in_anomaly_series(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/timeseries", params={"metric": "anomaly", "municipality": "DC6"})).json()
    xs = [p[0] for s in body["series"] for p in s["data"]]
    assert "2026-09-01" not in xs and "2026-08-01" in xs
    assert body["asOf"] == "2026-08-01"


@pytest.mark.parametrize(
    ("kind", "key", "model"),
    [
        ("timeseries", "monthly", c.SeriesSet),
        ("timeseries", "anomaly", c.SeriesSet),
        ("timeseries", "spi-spei", c.SeriesSet),
        ("timeseries", "intensity", c.SeriesSet),
        ("timeseries", "dry-wet", c.SeriesSet),
        ("breakdown", "seasonal", c.CategorySet),
        ("breakdown", "events", c.CategorySet),
        ("table", "extremes", c.TableSet),
        ("table", "statistics", c.TableSet),
        ("table", "sources", c.TableSet),
        ("table", "provinces", c.TableSet),
    ],
)
async def test_datasets_validate(client: httpx.AsyncClient, kind: str, key: str, model: type[c.ContractModel]) -> None:
    r = await client.get(f"{B}/{kind}", params={"metric": key, "municipality": "DC44"})
    assert r.status_code == 200, r.text
    body = r.json()
    model.model_validate(body)
    assert body.get("source"), "every dataset names its source tables"


@pytest.mark.parametrize(
    "layer",
    ["total", "anomaly", "monthly", "map", "spi1", "spi3", "spi6", "spi12", "spei6", "cdd", "r20", "municipal-total"],
)
async def test_map_layers(client: httpx.AsyncClient, layer: str) -> None:
    r = await client.get(f"{B}/map", params={"layer": layer, "province": "WC"})
    assert r.status_code == 200, r.text
    body = r.json()
    c.MapLayer.model_validate(body)
    assert body["kind"] == "choropleth" and body["level"] == "municipality" and body["geometry"] == "districts"
    assert set(body["values"]) == {"CPT"}  # only the fixture's WC district, keyed by MDB code


async def test_map_cdd_matches_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get(f"{B}/map", params={"layer": "cdd"})).json()
    ((last_wet,),) = fixture_db(
        """SELECT max("Date"::date) FROM "6.1_core_rainfall_variables_master"
           WHERE "District_Name" = 'Namakwa' AND "Daily_Rainfall_Total_mm" >= 1"""
    )
    import datetime as dt

    assert body["values"]["DC6"] == (dt.date(2026, 9, 6) - last_wet).days


async def test_station_series(client: httpx.AsyncClient) -> None:
    ok = await client.get(f"{B}/timeseries", params={"metric": "station", "entityId": "CPT"})
    assert ok.status_code == 200 and ok.json()["aggregation"] == "district"
    assert (await client.get(f"{B}/timeseries", params={"metric": "station"})).status_code == 400
    assert (await client.get(f"{B}/timeseries", params={"metric": "station", "entityId": "XX"})).status_code == 404


async def test_entities(client: httpx.AsyncClient) -> None:
    all_ = (await client.get(f"{B}/entities")).json()
    assert all_["type"] == "district" and all_["total"] == 52
    wc = (await client.get(f"{B}/entities", params={"province": "WC"})).json()
    assert {i["id"] for i in wc["items"]} == {"CPT", "DC1", "DC2", "DC3", "DC4", "DC5"}
    d = (await client.get(f"{B}/entities/CPT")).json()
    c.EntityDetail.model_validate(d)
    assert d["image"] == "municipality" and d["status"]["scale"] == "drought"
    assert (await client.get(f"{B}/entities/NOPE")).status_code == 404


@pytest.mark.parametrize(
    ("path", "params", "status", "code"),
    [
        ("forecast", {"metric": "rainfall"}, 501, "DATA_UNAVAILABLE"),
        ("forecast", {"metric": "spi"}, 501, "DATA_UNAVAILABLE"),
        ("map", {"layer": "forecast-2"}, 501, "DATA_UNAVAILABLE"),
        ("summary", {"catchment": "C8"}, 501, "DATA_UNAVAILABLE"),
        ("summary", {"municipality": "EC101"}, 400, "BAD_REQUEST"),
        ("summary", {"province": "XX"}, 400, "BAD_REQUEST"),
        ("summary", {"period": "7M"}, 400, "BAD_REQUEST"),
        ("summary", {"from": "2026-13-01"}, 400, "BAD_REQUEST"),
        ("table", {"metric": "provinces", "limit": "0"}, 400, "BAD_REQUEST"),
        ("forecast", {"metric": "nope"}, 404, "NOT_FOUND"),
        ("timeseries", {"metric": "nope"}, 404, "NOT_FOUND"),
        ("map", {"layer": "nope"}, 404, "NOT_FOUND"),
        ("matrix", {"metric": "anything"}, 404, "NOT_FOUND"),
        ("timeseries", {}, 400, "BAD_REQUEST"),
    ],
)
async def test_errors(client: httpx.AsyncClient, path: str, params: dict[str, str], status: int, code: str) -> None:
    r = await client.get(f"{B}/{path}", params=params)
    assert r.status_code == status, r.text
    body = r.json()
    assert isinstance(body["error"], str) and body["code"] == code
    if status == 501:
        assert body["detail"] and body["missing"]


async def test_wma_flagged_as_overlap(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/summary", params={"wma": "WMA9"})).json()
    assert body["aggregation"] == "district-overlap" and body["scope"]["level"] == "wma"


async def test_custom_window(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (
        await client.get(f"{B}/summary", params={"municipality": "DC6", "from": "2025-01-01", "to": "2025-12-31"})
    ).json()
    ((total,),) = fixture_db(
        """SELECT round(sum("Daily_Rainfall_Total_mm")::numeric, 1) FROM "6.1_core_rainfall_variables_master"
           WHERE "District_Name" = 'Namakwa' AND "Date" BETWEEN '2025-01-01' AND '2025-12-31'"""
    )
    assert kpis(body)["total"]["value"] == float(total)


async def test_health_reports_rainfall_live(client: httpx.AsyncClient) -> None:
    mods = {m["module"]: m for m in (await client.get("/api/v1/health")).json()["modules"]}
    assert mods["rainfall"]["status"] == "live"


async def test_seasonal_breakdown_uses_the_rainfall_tables_own_seasons(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    """B11: OND / JFM / AMJ / JAS from 6.1 (spec §6.1), read from the stored columns, not recomputed."""
    body = (await client.get(f"{B}/breakdown", params={"metric": "seasonal", "municipality": "DC44"})).json()
    s = {x["key"]: dict(zip(body["categories"], x["values"], strict=True)) for x in body["series"]}
    assert list(s) == ["jfm", "amj", "jas", "ond"]
    assert body["source"] == ["6.1_core_rainfall_variables_master"]
    ((ond, jfm),) = fixture_db(
        """SELECT round(max(NULLIF("Seasonal_Total_OND_mm", '')::numeric), 1),
                  round(max("Seasonal_Total_JFM_mm")::numeric, 1)
           FROM "6.1_core_rainfall_variables_master" WHERE "District_Name" = 'Alfred Nzo' AND "Year" = 2025"""
    )
    assert (s["ond"]["2025"], s["jfm"]["2025"]) == (float(ond), float(jfm))


async def test_climate_seasons_stay_djf_mam_jja_son(client: httpx.AsyncClient) -> None:
    body = (
        await client.get("/api/v1/climate/breakdown", params={"metric": "map-seasonality", "municipality": "DC44"})
    ).json()
    assert [x["name"] for x in body["series"]] == ["Annual", "DJF", "MAM", "JJA", "SON"]
    assert "4_3_mean_annual_precipitation_master" in body["source"]


async def test_current_dry_spell_is_days_since_the_last_wet_day(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    """B13: per district, the number of days since the last day with ≥ 1 mm up to the newest record (the map layer
    shows the district values, the KPI their area-weighted mean)."""
    body = (await client.get(f"{B}/map", params={"layer": "cdd"})).json()
    rows = fixture_db(
        """WITH n AS (SELECT max("Date")::date AS d FROM "6.1_core_rainfall_variables_master")
           SELECT "District_Name", (SELECT d FROM n) - max("Date")::date
           FROM "6.1_core_rainfall_variables_master" WHERE "Daily_Rainfall_Total_mm" >= 1 GROUP BY 1"""
    )
    codes = {"Alfred Nzo": "DC44", "City of Cape Town": "CPT", "Namakwa": "DC6"}
    assert {codes[n]: float(d) for n, d in rows} == body["values"]
    kpi = next(
        k for k in (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()["kpis"] if k["id"] == "cdd"
    )
    assert kpi["value"] == body["values"]["DC6"]


async def test_sources_give_each_columns_real_resolution(client: httpx.AsyncClient) -> None:
    """B14: TAMSAT is stored on daily rows but holds one value per month — the sources table says monthly."""
    body = (await client.get(f"{B}/table", params={"metric": "sources"})).json()
    rows = {r["id"].rsplit(".", 1)[1]: r for r in body["rows"]}
    tamsat = rows["TAMSAT_Satellite_Rainfall_Estimate_mm_dekad"]
    assert tamsat["cadence"] == "monthly" and "dekad" in tamsat["source"]
    assert rows["Daily_Rainfall_Total_mm"]["cadence"] == "daily"
    assert rows["CHIRPS_Daily_Rainfall_Estimate_mm_day"]["cadence"] == "daily"
