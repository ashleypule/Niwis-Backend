"""Database view endpoints (A2 / A3) on the recorded fixture DB (DC44, CPT, DC6)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.modules import IMPLEMENTED_MODULES
from app.registry.catalogue import get_catalogue
from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
CAT = get_catalogue()
GROUPS = [(cat.modules[0], cat, g) for cat in CAT.categories for g in cat.groups]


def path(module: str, table_id: str, group_id: str) -> str:
    return f"/api/v1/{module}/category/{table_id}/{group_id}"


def series(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["key"]: s for s in body["series"]}


@pytest.mark.parametrize("module", sorted(IMPLEMENTED_MODULES))
async def test_categories_list_every_table_of_the_module(client: httpx.AsyncClient, module: str) -> None:
    r = await client.get(f"/api/v1/{module}/categories")
    assert r.status_code == 200, r.text
    body = c.CategoryList.model_validate(r.json()).dump()
    mine = CAT.for_module(module)
    assert [t["table"] for t in body["tables"]] == [x.table for x in mine]
    for t, cat in zip(body["tables"], mine, strict=True):
        assert t["counts"] == {
            s: sum(1 for x in cat.columns if x.status == s) for s in ("live", "withheld", "duplicate")
        }
        assert t["specTag"] == f"§{cat.spec}"
        assert [g["path"] for g in t["groups"]] == [path(module, cat.id, g.id) for g in cat.groups]
        for v in t["variables"]:
            assert v["label"] and v["source"] == f"{cat.table}.{v['column']}" and "defaultVisible" in v
            if v["status"] == "withheld":
                assert v["reason"] and "asOf" not in v
            if v["status"] == "duplicate":
                assert v["duplicateOf"] and "group" not in v
            if v["status"] == "live":
                assert v["asOf"] and isinstance(v["isStale"], bool)


async def test_meteorological_table_is_on_the_drought_page(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/drought/categories")).json()
    met = next(t for t in body["tables"] if t["table"] == "meteorological_drought_master")
    assert met["label"] == "Meteorological Drought" and met["specTag"] == "§3.1"
    labels = {g["label"] for g in met["groups"]}
    assert "Evapotranspiration (mm/day)" in labels and "SPEI (index)" in labels
    rh = next(v for v in met["variables"] if v["column"] == "relative_humidity_pct")
    assert (rh["label"], rh["unit"], rh["status"]) == ("Relative Humidity (%)", "%", "live")


@pytest.mark.parametrize(("module", "cat", "g"), GROUPS, ids=[f"{x[1].id}/{x[2].id}" for x in GROUPS])
async def test_every_group_serves_all_its_columns(client: httpx.AsyncClient, module: str, cat: Any, g: Any) -> None:
    r = await client.get(path(module, cat.id, g.id), params={"vars": "all", "municipality": "DC44"})
    assert r.status_code == 200, r.text
    body = c.SeriesSet.model_validate(r.json()).dump()
    assert [s["key"] for s in body["series"]] == list(g.columns)
    # axis rule: one axis, and every series with a unit carries the axis unit
    assert len(body["axes"]) == 1 and body["axes"][0]["unit"] == g.unit
    cols = {x.column: x for x in cat.columns}
    assert len({cols[k].unit for k in g.columns}) == 1
    unavailable = {u["field"] for u in body.get("unavailable", [])}
    for s in body["series"]:
        x = cols[s["key"]]
        assert s["name"] == x.label and s["source"] == [f"{cat.table}.{x.column}"] and s["axis"] == 0
        assert s.get("unit") == x.unit and s["status"] == x.status
        if x.status == "withheld":
            assert all(p[1] is None for p in s["data"]) and f"series.{x.column}" in unavailable
    assert body["source"] == [cat.table]


async def test_default_columns_are_the_groups_default_visible(client: httpx.AsyncClient) -> None:
    cat = next(x for x in CAT.categories if x.table == "6.1_core_rainfall_variables_master")
    g = cat.group("seasonal-total-mm")
    assert g is not None
    body = (await client.get(path("rainfall", cat.id, g.id), params={"municipality": "DC44"})).json()
    assert [s["key"] for s in body["series"]] == g.default_visible
    one = (await client.get(path("rainfall", cat.id, g.id), params={"vars": "Seasonal_Total_JAS_mm"})).json()
    assert [s["key"] for s in one["series"]] == ["Seasonal_Total_JAS_mm"]


async def test_errors(client: httpx.AsyncClient) -> None:
    r = await client.get(path("rainfall", "no-such-table", "x"))
    assert r.status_code == 404 and r.json()["issues"][0]["param"] == "tableId"
    r = await client.get(path("rainfall", "core-rainfall-variables", "no-such-group"))
    assert r.status_code == 404 and "seasonal-total-mm" in r.json()["issues"][0]["allowed"]
    r = await client.get(path("runoff", "core-rainfall-variables", "seasonal-total-mm"))  # not a runoff table
    assert r.status_code == 404
    r = await client.get(path("rainfall", "core-rainfall-variables", "seasonal-total-mm"), params={"vars": "Nope_mm"})
    assert r.status_code == 400
    issue = r.json()["issues"][0]
    assert issue["invalid"] == ["Nope_mm"] and "Seasonal_Total_OND_mm" in issue["allowed"]
    r = await client.get(path("rainfall", "core-rainfall-variables", "seasonal-total-mm"), params={"entityId": "XX9"})
    assert r.status_code == 404


async def test_annual_values_equal_the_column_and_skip_the_partial_year(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (
        await client.get(
            path("rainfall", "core-rainfall-variables", "seasonal-total-mm"),
            params={"vars": "Seasonal_Total_JFM_mm", "municipality": "DC44"},
        )
    ).json()
    got = dict(map(tuple, series(body)["Seasonal_Total_JFM_mm"]["data"]))
    rows = fixture_db(
        """SELECT "Year", max("Seasonal_Total_JFM_mm") FROM "6.1_core_rainfall_variables_master"
           WHERE "District_Name" = 'Alfred Nzo' GROUP BY 1 ORDER BY 1"""
    )
    sql = {f"{y}-01-01": round(float(v), 2) for y, v in rows}
    assert "2026-01-01" in sql and "2026-01-01" not in got  # the partial year is in the table, never served
    assert got == {k: v for k, v in sql.items() if k != "2026-01-01"}
    assert body["series"][0]["asOf"] == "2025-01-01" and body["aggregation"] == "district"


async def test_monthly_values_equal_the_column(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (
        await client.get(
            path("rainfall", "rainfall-derived-drought-indices", "spi-rai-index"),
            params={"vars": "RAI_Rainfall_Anomaly_Index,SPI_3_Short_Term_Agricultural_Drought", "municipality": "DC6"},
        )
    ).json()
    s = series(body)
    pts = dict(map(tuple, s["RAI_Rainfall_Anomaly_Index"]["data"]))
    assert len(pts) == 12 and max(pts) == "2026-08-01"  # 12 complete months; 2026-09 is partial
    ((rai,),) = fixture_db(
        """SELECT round("RAI_Rainfall_Anomaly_Index"::numeric, 2) FROM "6.2_rainfall_derived_drought_indices_master"
           WHERE "District_Name" = 'Namakwa' AND "Year" = 2026 AND "Month" = 8"""
    )
    assert pts["2026-08-01"] == float(rai)
    assert "unit" not in s["RAI_Rainfall_Anomaly_Index"] and body["axes"][0]["unit"] == "index"


async def test_daily_values_and_downsampling(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    p = path("rainfall", "core-rainfall-variables", "daily-rainfall-total-mm")
    daily = (await client.get(p, params={"municipality": "CPT"})).json()
    pts = dict(map(tuple, daily["series"][0]["data"]))
    assert len(pts) == 365 and max(pts) == "2026-09-06"
    ((v,),) = fixture_db(
        """SELECT round("Daily_Rainfall_Total_mm"::numeric, 2) FROM "6.1_core_rainfall_variables_master"
           WHERE "District_Name" = 'City of Cape Town' AND "Date" = '2026-07-15'"""
    )
    assert pts["2026-07-15"] == float(v)
    hist = (await client.get(p, params={"municipality": "CPT", "period": "historical"})).json()
    assert all(x[0].endswith("-01") for x in hist["series"][0]["data"]) and "downsampled" in hist["note"]


async def test_repeated_monthly_values_are_read_once_per_month(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (
        await client.get(
            path("rainfall", "core-rainfall-variables", "tamsat-satellite-rainfall-estimate-mm-dekad"),
            params={"municipality": "DC44"},
        )
    ).json()
    s = body["series"][0]
    assert s["resolution"] == "monthly" and s["unit"] == "mm/dekad"
    pts = dict(map(tuple, s["data"]))
    ((v,),) = fixture_db(
        """SELECT round(max("TAMSAT_Satellite_Rainfall_Estimate_mm_dekad")::numeric, 2)
           FROM "6.1_core_rainfall_variables_master" WHERE "District_Name" = 'Alfred Nzo' AND "Year" = 2026
           AND "Month" = 3"""
    )
    assert pts["2026-03-01"] == float(v)


async def test_entity_selects_one_district_whatever_the_scope(client: httpx.AsyncClient) -> None:
    p = path("rainfall", "rainfall-derived-drought-indices", "spi-rai-index")
    a = (await client.get(p, params={"entityId": "DC44"})).json()
    a_wc = (await client.get(p, params={"entityId": "DC44", "province": "WC"})).json()
    b = (await client.get(p, params={"entityId": "DC6"})).json()
    muni = (await client.get(p, params={"municipality": "DC44"})).json()
    assert a["series"] == a_wc["series"] == muni["series"] != b["series"]
    assert a["aggregation"] == "district"


async def test_withheld_group_says_why(client: httpx.AsyncClient) -> None:
    body = (
        await client.get(path("drought", "hydrological-drought", "dam-storage-mm3"), params={"municipality": "CPT"})
    ).json()
    s = body["series"][0]
    assert all(p[1] is None for p in s["data"]) and len(s["data"]) == 12
    assert "plausibility" in body["unavailable"][0]["reason"] and s["status"] == "withheld"
