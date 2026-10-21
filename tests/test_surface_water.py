"""Surface Water Monitoring (spec §9): district flows from 9_1's daily family (DECISIONS D10), values vs SQL."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/surface-water"
T = '"9_1_hydrological_station_master"'


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


async def test_discharge_kpi_is_the_mean_daily_flow_in_m3s(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    k = kpi(body, "discharge")
    assert k["unit"] == "m³/s" and k["asOf"] == "2026-08-01"  # Sep 2026 is incomplete (9_1 ends 24 Sep)
    ((v,),) = fixture_db(
        f"SELECT avg(daily_mean_flow) / 86400 FROM {T} WHERE district_name = 'Namakwa' "
        "AND date BETWEEN '2026-08-01' AND '2026-08-31'"
    )
    assert k["value"] == round(float(v), 1)
    assert kpi(body, "stations")["value"] is None and "No coordinates" in kpi(body, "stations")["unavailableReason"]
    assert kpi(body, "flood")["unavailableReason"] and kpi(body, "eflow")["unavailableReason"]


async def test_sediment_and_stage_are_the_stored_monthly_values(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    ((ssc, stage),) = fixture_db(
        f"SELECT max(suspended_sediment_concentration), max(stage_height) FROM {T} "
        "WHERE district_name = 'Namakwa' AND year = 2026 AND month = 8"
    )
    assert kpi(body, "sediment")["value"] == round(float(ssc))
    assert kpi(body, "stage")["value"] == round(float(stage), 2)


async def test_high_low_flow_classes_follow_the_districts_own_percentiles(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    """The fixture holds 9_1 from 2024: the reference period is its full years, 2024–2025."""
    m = (await client.get(f"{B}/map", params={"layer": "flow-status"})).json()
    rows = fixture_db(
        f"""WITH mm AS (SELECT district_name n, year y, month mo, avg(daily_mean_flow) / 86400 v FROM {T}
                        WHERE date <= '2026-08-31' GROUP BY 1, 2, 3),
                q AS (SELECT n, percentile_cont(0.1) WITHIN GROUP (ORDER BY v) q10,
                             percentile_cont(0.9) WITHIN GROUP (ORDER BY v) q90
                      FROM mm WHERE y IN (2024, 2025) GROUP BY 1)
            SELECT mm.n, CASE WHEN v > q90 THEN 'High Flow' WHEN v < q10 THEN 'Low Flow' ELSE 'Normal' END
            FROM mm JOIN q USING (n) WHERE y = 2026 AND mo = 8"""
    )
    codes = {"Alfred Nzo": "DC44", "City of Cape Town": "CPT", "Namakwa": "DC6"}
    assert m["values"] == {codes[n]: cls for n, cls in rows}
    bd = (await client.get(f"{B}/breakdown", params={"metric": "status"})).json()
    assert sum(bd["series"][0]["values"]) == 3


async def test_flow_anomaly_map_vs_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    m = (await client.get(f"{B}/map", params={"layer": "flow-anomaly"})).json()
    where = f"FROM {T} WHERE district_name = 'Namakwa' AND month = 8"
    ((cur, normal),) = fixture_db(  # normal = mean of the August means of the fixture's full years (2024, 2025)
        f"""SELECT (SELECT avg(daily_mean_flow) {where} AND year = 2026),
                   (SELECT avg(m) FROM (SELECT avg(daily_mean_flow) m {where}
                                        AND year IN (2024, 2025) GROUP BY year) x)"""
    )
    assert m["values"]["DC6"] == round((float(cur) - float(normal)) / float(normal) * 100)
    assert m["geometry"] == "districts" and m["unit"] == "%"


async def test_entity_datasets_follow_the_district(client: httpx.AsyncClient) -> None:
    a = (await client.get(f"{B}/timeseries", params={"metric": "discharge", "entityId": "DC6"})).json()
    b = (await client.get(f"{B}/timeseries", params={"metric": "discharge", "entityId": "CPT"})).json()
    assert a["title"].endswith("Namakwa") and a["series"][0]["data"] != b["series"][0]["data"]
    card = (await client.get(f"{B}/entities/DC6")).json()
    labels = [f["label"] for f in card["fields"]]
    assert any(lb.startswith("Mean flow") for lb in labels) and any(lb.startswith("Catchment area") for lb in labels)


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("timeseries", "baseflow"),
        ("breakdown", "eflow"),
        ("table", "alerts"),
        ("map", "stations"),
        ("forecast", "flow"),
    ],
)
async def test_unavailable_keys(client: httpx.AsyncClient, kind: str, key: str) -> None:
    r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
    assert r.status_code == 501 and r.json()["detail"]


async def test_runoff_layers_are_the_runoff_modules(client: httpx.AsyncClient) -> None:
    a = (await client.get(f"{B}/map", params={"layer": "annual-runoff"})).json()
    b = (await client.get("/api/v1/runoff/map", params={"layer": "annual-runoff"})).json()
    assert a["values"] == b["values"]


async def test_monthly_family_and_daily_discharge_before_2026_are_withheld(client: httpx.AsyncClient) -> None:
    cat = (await client.get(f"{B}/categories")).json()
    t = next(x for x in cat["tables"] if x["table"] == "9_1_hydrological_station_master")
    st = {v["column"]: v for v in t["variables"]}
    assert st["monthly_mean_flow"]["status"] == "withheld" and "1/63" in st["monthly_mean_flow"]["reason"]
    assert st["daily_mean_flow"]["status"] == "live"
    assert "Served from Jan 2026" in st["river_discharge_q"]["note"]
    eo = next(x for x in cat["tables"] if x["table"] == "9_3_sansa_earth_observation_master")
    assert eo["counts"]["live"] == 0


async def test_no_kpi_sends_a_null_unit(client: httpx.AsyncClient) -> None:
    """The contract's KPI unit is a string or absent (the frontend's zod schema rejects null)."""
    for module in ("surface-water", "dams"):
        body = (await client.get(f"/api/v1/{module}/summary")).json()
        assert all(k.get("unit", "") is not None for k in body["kpis"]), module
