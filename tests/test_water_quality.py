"""Water Quality Monitoring (spec §12): guideline comparisons, CCME WQI, × guideline maps; values vs SQL."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.modules.water_quality.service import ccme_wqi, passes, ratio
from tests.conftest import requires_db

B = "/api/v1/water-quality"


def test_ccme_wqi() -> None:
    assert ccme_wqi([("ph", 7.0), ("ec", 50.0)]) == 100  # every test within guideline
    assert ccme_wqi([]) is None
    # one of two parameters fails once in two tests: F1 = 50, F2 = 50, excursion 1 (2 × limit) → F3 = 33.3
    v = ccme_wqi([("ec", 300.0), ("ph", 7.0)])
    assert v is not None and round(v, 1) == round(100 - (50**2 + 50**2 + (0.5 / 0.015) ** 2) ** 0.5 / 1.732, 1)


def test_guidelines_and_ratios() -> None:
    assert passes("ph", 7.0) and not passes("ph", 9.5) and not passes("do", 4.0)
    assert ratio("ec", 75) == 0.5 and ratio("do", 10) == 0.5  # DO is a minimum: limit / value


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


@pytest.mark.db
@requires_db
async def test_compliance_is_the_share_of_values_within_guideline(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    month = kpi(body, "compliance")["asOf"]
    y, m = int(month[:4]), int(month[5:7])
    ((ph, ec, po4),) = fixture_db(
        f"""SELECT c."pH", p."Electrical Conductivity (EC)", c."Orthophosphate (PO43-)"
            FROM "12.2_chemical_water_quality_master" c JOIN "12.1_physical_water_quality_master" p
            USING (district_name, year, month) WHERE district_name = 'Namakwa' AND year = {y} AND month = {m}"""
    )
    latest = (await client.get(f"{B}/table", params={"metric": "latest", "entityId": "DC6"})).json()
    rows = {r["id"]: r for r in latest["rows"]}
    assert rows["ph"]["value"] == round(float(ph), 3) and rows["ec"]["value"] == round(float(ec), 3)
    assert rows["phosphate"]["status"] == ("Within guideline" if float(po4) <= 0.125 else "Exceeds guideline")
    share = sum(r["status"] == "Within guideline" for r in latest["rows"]) / len(latest["rows"]) * 100
    assert kpi(body, "compliance")["value"] == round(share)
    assert set(kpi(body, "compliance")["source"]) >= {
        "12.1_physical_water_quality_master",
        "12.3_biological_water_quality_master",
    }


@pytest.mark.db
@requires_db
async def test_ratio_map_vs_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    m = (await client.get(f"{B}/map", params={"layer": "nitrate"})).json()
    y, mo = int(m["date"][:4]), int(m["date"][5:7])
    ((v,),) = fixture_db(
        f"""SELECT "Nitrate (NO3-N)" FROM "12.2_chemical_water_quality_master"
            WHERE district_name = 'City of Cape Town' AND year = {y} AND month = {mo}"""
    )
    assert m["values"]["CPT"] == round(float(v) / 6, 2) and m["unit"] == "× guideline"


@pytest.mark.db
@requires_db
async def test_wqi_and_selectors(client: httpx.AsyncClient) -> None:
    m = (await client.get(f"{B}/map", params={"layer": "wqi"})).json()
    assert all(0 <= v <= 100 for v in m["values"].values())
    poorest = (await client.get(f"{B}/table", params={"metric": "ranking", "order": "poorest"})).json()
    best = (await client.get(f"{B}/table", params={"metric": "ranking", "order": "best"})).json()
    assert poorest["rows"][0]["wqi"] <= best["rows"][0]["wqi"]
    a = (
        await client.get(f"{B}/timeseries", params={"metric": "group", "group": "nutrients", "entityId": "DC6"})
    ).json()
    assert a["title"].endswith("Namakwa") and {s["key"] for s in a["series"]} == {
        "wqc_nitrate_no3_n",
        "wqc_orthophosphate_po43",
        "wqc_ammonia_nh3_n",
    }


@pytest.mark.db
@requires_db
@pytest.mark.parametrize(
    ("kind", "key"), [("map", "lead"), ("breakdown", "by-type"), ("table", "counts"), ("forecast", "wqi")]
)
async def test_unavailable_keys(client: httpx.AsyncClient, kind: str, key: str) -> None:
    r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
    assert r.status_code == 501 and r.json()["detail"]
