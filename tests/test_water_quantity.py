"""Water-quantity endpoints on the recorded fixture DB (17_1: the 16 transfer-system districts; 17_2: national;
11.2 / 11.3 and 16.1 for DC44, CPT, DC6)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/water-quantity"


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


async def test_summary_national(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    r = await client.get(f"{B}/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    c.KpiSet.model_validate(body)
    ids = [k["id"] for k in body["kpis"]]
    assert ids == [
        "availability",
        "surface",
        "groundwater",
        "abstraction",
        "allocation",
        "transfers",
        "demand",
        "stress",
    ]
    # transfers: the four schemes' rates × days in the month over the last 12 complete months, all 16 districts
    t = kpi(body, "transfers")
    assert t["value"] and t["value"] > 0 and t["unit"] == "Mm³/yr"
    ((total,),) = fixture_db(  # the transfer columns are served to Dec 2024 (zero-filled afterwards, valid_until)
        """SELECT sum(("Katse_to_Vaal_Daily_Transfer_m3_day" + "Tugela_Vaal_Daily_Transfer_m3_day"
                   + "Orange_River_System_Transfer_m3_day" + "Thukela_Vaal_Emergency_Regular_Transfer_m3_day")
                   * extract(day FROM (make_date(year, month, 1) + interval '1 month - 1 day'))) / 1e6
           FROM "17_1_water_quantity_assessment_transfers_master" WHERE year = 2024"""
    )
    assert t["asOf"] == "2024-12-01" and "Jan 2024 – Dec 2024" in t["sublabel"]
    assert abs(t["value"] - float(total)) <= 1.0  # rounding to whole Mm³
    for kid in ("availability", "allocation", "demand", "stress"):
        assert kpi(body, kid)["value"] is None and kpi(body, kid)["unavailableReason"]
    assert kpi(body, "surface")["value"] is None  # a sum needs every district: the fixture holds three


async def test_summary_province_and_district(client: httpx.AsyncClient) -> None:
    kzn = (await client.get(f"{B}/summary", params={"province": "KZN"})).json()
    assert kpi(kzn, "transfers")["value"] > 0  # Amajuba is a transfer-system district
    wc = (await client.get(f"{B}/summary", params={"province": "WC"})).json()
    assert kpi(wc, "transfers")["value"] is None and "16 districts" in kpi(wc, "transfers")["unavailableReason"]
    cpt = (await client.get(f"{B}/summary", params={"municipality": "CPT"})).json()
    assert kpi(cpt, "surface")["value"] is not None and kpi(cpt, "groundwater")["value"] is not None


async def test_storage_is_national_only(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    r = await client.get(f"{B}/timeseries", params={"metric": "storage", "period": "historical"})
    assert r.status_code == 200, r.text
    body = r.json()
    c.SeriesSet.model_validate(body)
    st = body["series"][0]["data"]
    ((_n, last),) = fixture_db(
        """SELECT count(*), max("Surface_Storage_Mm3") FILTER (WHERE "Year" = 2024 AND "Month" = 6)
           FROM "17_2_national_water_balance_master" WHERE make_date("Year", "Month", 1) <= CURRENT_DATE"""
    )
    assert len(st) >= 300 and dict(st)["2024-06-01"] == round(float(last))
    assert any(u["field"] == "series.pct" for u in body["unavailable"])  # % FSC: no capacity in the database
    prov = await client.get(f"{B}/timeseries", params={"metric": "storage", "province": "GP"})
    assert prov.status_code == 501 and "South Africa only" in prov.json()["detail"]


async def test_groundwater_series_breakdown_table_maps(client: httpx.AsyncClient) -> None:
    gw = await client.get(f"{B}/timeseries", params={"metric": "groundwater", "municipality": "DC6"})
    assert gw.status_code == 200, gw.text
    keys = [s["key"] for s in gw.json()["series"]]
    assert keys == ["recharge", "abstraction", "net"] and gw.json()["series"][0]["data"]
    bd = await client.get(f"{B}/breakdown", params={"metric": "transfers"})
    assert bd.status_code == 200, bd.text
    assert len(bd.json()["categories"]) == 5 and all(v is not None for v in bd.json()["series"][0]["values"])
    tbl = await client.get(f"{B}/table", params={"metric": "summary"})
    assert tbl.status_code == 200, tbl.text
    rows = {r["id"]: r for r in tbl.json()["rows"]}
    assert rows["storage"]["value"] and rows["transfers"]["value"] and rows["aet"]["value"]
    m = await client.get(f"{B}/map", params={"layer": "transfers", "province": "KZN"})
    assert m.status_code == 200 and "DC25" in m.json()["values"]
    av = await client.get(f"{B}/map", params={"layer": "availability", "province": "WC"})
    assert av.status_code == 200 and set(av.json()["values"]) == {"CPT"}
    ent = await client.get(f"{B}/entities/DC25")
    assert ent.status_code == 200 and ent.json()["image"] == "municipality"


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("timeseries", "surface"),
        ("breakdown", "waterfall"),
        ("breakdown", "sector"),
        ("breakdown", "allocation"),
        ("table", "catchments"),
        ("map", "stress"),
        ("map", "demand-density"),
    ],
)
async def test_unavailable_keys(client: httpx.AsyncClient, kind: str, key: str) -> None:
    r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
    assert r.status_code == 501 and r.json()["detail"]
    assert (await client.get(f"{B}/forecast", params={"metric": "x"})).status_code == 404
