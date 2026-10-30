"""IWRM endpoints on the recorded fixture DB (16_1 for DC44, CPT, DC6; 17_1 for the transfer districts)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.spatial import get_spatial
from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/iwrm"


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


async def test_summary_district_is_runoff_plus_recharge(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    r = await client.get(f"{B}/summary", params={"municipality": "DC6"})
    assert r.status_code == 200, r.text
    body = r.json()
    c.KpiSet.model_validate(body)
    ((mar, rech, reserve, ret, losses, balance),) = fixture_db(
        """SELECT "Mean_Annual_Runoff_MAR_million_m3_yr", "Mean_Annual_Groundwater_Recharge_mm_yr",
                  "Ecological_Reserve_Water_Requirement_m3_yr", "Return_Flow_Recycled_Water_million_m3_yr",
                  "System_Water_Losses_pct", "National_Water_Balance_million_m3_yr"
           FROM "16_1_complete_wrm_matrix_master" WHERE district_name = 'Namakwa' AND year = 2025"""
    )
    area = (get_spatial().district_areas() or {})["DC6"]
    assert kpi(body, "availability")["value"] == round(float(mar) + float(rech) * area / 1000)
    assert kpi(body, "reserve")["value"] == round(float(reserve) / 1e6)
    assert kpi(body, "recycled")["value"] == round(float(ret))
    assert kpi(body, "losses")["value"] == round(float(losses), 1)
    assert kpi(body, "balance")["value"] == round(float(balance))
    for kid in ("demand", "stress", "status"):
        assert kpi(body, kid)["value"] is None and kpi(body, kid)["unavailableReason"]
    nat = (await client.get(f"{B}/summary")).json()
    assert kpi(nat, "availability")["value"] is None  # a sum needs every district: the fixture holds three


async def test_series_breakdowns_tables_maps(client: httpx.AsyncClient) -> None:
    av = await client.get(f"{B}/timeseries", params={"metric": "availability", "municipality": "CPT"})
    assert av.status_code == 200, av.text
    keys = [s["key"] for s in av.json()["series"]]
    assert keys == ["total", "surface", "groundwater", "recycled", "demand"]
    assert av.json()["series"][0]["data"] and any(u["field"] == "series.demand" for u in av.json()["unavailable"])
    nrw = await client.get(f"{B}/timeseries", params={"metric": "nrw", "municipality": "CPT"})
    assert nrw.status_code == 200 and all(0 <= v[1] <= 100 for v in nrw.json()["series"][0]["data"])
    for metric in ("transfers", "reserve-compliance", "recycled"):
        bd = await client.get(f"{B}/breakdown", params={"metric": metric})
        assert bd.status_code == 200, bd.text
        assert len(bd.json()["categories"]) == 9
    wm = await client.get(f"{B}/table", params={"metric": "wmas"})
    assert wm.status_code == 200, wm.text
    assert len(wm.json()["rows"]) == 9 and {u["field"] for u in wm.json()["unavailable"]} >= {"rows.demand", "rows.wsi"}
    for layer in ("availability", "transfers", "swai"):
        m = await client.get(f"{B}/map", params={"layer": layer, "province": "EC"})
        assert m.status_code == 200 and set(m.json()["values"]) == {"DC44"}, layer


@pytest.mark.parametrize(
    ("kind", "key"),
    [("breakdown", "waterfall"), ("breakdown", "sector"), ("table", "catchments"), ("map", "stress")],
)
async def test_unavailable_keys(client: httpx.AsyncClient, kind: str, key: str) -> None:
    r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
    assert r.status_code == 501 and r.json()["detail"]
