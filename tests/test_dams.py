"""Dam & Reservoir Status (spec §8): storage from 8_1 fails the plausibility check (docs/DECISIONS.md D2), so every
storage / flow / alert panel returns the unavailable convention with that reason; the 52 district reservoirs and their
catchment rainfall / soil moisture are served."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core import districts
from app.core.thresholds import thresholds
from app.modules.dams.service import alert_for, days_to_empty
from tests.conftest import requires_db

D = "/api/v1/dams"
FROZEN = "51 of 52 districts hold the same storage"


@pytest.mark.parametrize(
    ("pct", "level"), [(None, None), (80.0, "None"), (49.9, "L1"), (30.0, "L1"), (29.9, "L2"), (14.9, "L3")]
)
def test_alert_levels_follow_spec_8_2(pct: float | None, level: str | None) -> None:
    a = thresholds()["storage_alerts"]
    assert (a["L1"], a["L2"], a["L3"]) == (50, 30, 15)
    assert alert_for(pct) == level


def test_days_to_empty() -> None:
    assert days_to_empty(300.0, 1.5) == 200
    assert days_to_empty(300.0, 0.0) is None  # filling or stable
    assert days_to_empty(300.0, -2.0) is None


def test_siyanda_is_not_a_district_alias() -> None:
    """D3: 'Siyanda' (old name of Z F Mgcawu) is a duplicate copy in 8_1 and must not be read as Z F Mgcawu."""
    assert districts.resolve("Siyanda") is None
    assert districts.resolve("Z F Mgcawu") is not None


@pytest.mark.db
@requires_db
async def test_summary_kpis_are_null_with_the_plausibility_reason(client: httpx.AsyncClient) -> None:
    r = await client.get(f"{D}/summary")
    assert r.status_code == 200
    body = r.json()
    ids = [k["id"] for k in body["kpis"]]
    assert ids == ["storage", "volume", "inflow", "outflow", "evaporation", "below", "above", "alerts"]
    for k in body["kpis"]:
        assert k["value"] is None and k["unavailableReason"], k["id"]
    listed = {u["field"]: u["reason"] for u in body["unavailable"]}
    assert FROZEN in listed["kpis.storage"]
    assert "spec §8.2" in listed["kpis.alerts"]


@pytest.mark.db
@requires_db
@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("timeseries", "storage-trend"),
        ("timeseries", "dam-storage"),
        ("timeseries", "flows"),
        ("breakdown", "by-wma"),
        ("table", "top10"),
        ("map", "storage"),
    ],
)
async def test_storage_panels_are_unavailable_with_the_reason(client: httpx.AsyncClient, kind: str, key: str) -> None:
    param = "layer" if kind == "map" else "metric"
    r = await client.get(f"{D}/{kind}", params={param: key})
    assert r.status_code == 501
    body = r.json()
    assert body["code"] == "DATA_UNAVAILABLE"
    assert FROZEN in body["detail"] or "constant value per district" in body["detail"]
    if kind == "map":
        assert "No coordinates" in body["detail"]


@pytest.mark.db
@requires_db
async def test_forecasts_and_unknown_keys(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{D}/forecast", params={"metric": "storage"})).status_code == 501
    r = await client.get(f"{D}/timeseries", params={"metric": "nope"})
    assert r.status_code == 404 and "storage-trend" in r.json()["issues"][0]["allowed"]


@pytest.mark.db
@requires_db
async def test_reservoir_entities_come_from_8_1(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{D}/entities")).json()
    assert body["type"] == "dam" and body["total"] == 3  # the three fixture districts
    names = {i["id"]: i["name"] for i in body["items"]}
    ((dam, river),) = fixture_db(
        """SELECT dam_name, river FROM "8_1_reservoir_operational_master" WHERE district_name = 'Alfred Nzo' LIMIT 1"""
    )
    assert names["DC44"] == dam
    card = (await client.get(f"{D}/entities/DC44")).json()
    fields = {f["label"]: f for f in card["fields"]}
    assert fields["River"]["value"] == river
    assert fields["Storage level"]["value"] is None
    assert any(u["field"] == "fields.Storage level" and FROZEN in u["reason"] for u in card["unavailable"])
    rain = next(f for label, f in fields.items() if label.startswith("Catchment rainfall"))
    assert rain["unit"] == "mm" and rain["value"] is not None
    assert rain["label"].endswith("(Aug 2026)")  # forward-filled from 1 Sep 2026: served to 31 Aug (registry)
    assert (await client.get(f"{D}/entities/DC1")).status_code == 404


@pytest.mark.db
@requires_db
async def test_month_total_equals_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    card = (await client.get(f"{D}/entities/DC6")).json()
    rain = next(f for f in card["fields"] if f["label"].startswith("Catchment rainfall"))
    ((total,),) = fixture_db(
        """SELECT sum(rainfall_mm_day) FROM "8_1_reservoir_operational_master"
           WHERE district_name = 'Namakwa' AND date BETWEEN '2026-08-01' AND '2026-08-31'"""
    )
    assert rain["value"] == round(float(total))


@pytest.mark.db
@requires_db
async def test_database_view_shows_8_1(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{D}/categories")).json()
    (t,) = body["tables"]
    assert t["table"] == "8_1_reservoir_operational_master"
    assert t["counts"] == {"live": 2, "withheld": 12, "duplicate": 0}
    status = {v["column"]: v["status"] for v in t["variables"]}
    assert status["storage_volume_m3"] == "withheld" and status["rainfall_mm_day"] == "live"


@pytest.mark.db
@requires_db
async def test_district_layers_from_the_live_8_1_columns(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    rain = (await client.get(f"{D}/map", params={"layer": "catchment-rainfall"})).json()
    assert rain["kind"] == "choropleth" and rain["geometry"] == "districts" and rain["unit"] == "mm"
    assert rain["date"] == "2026-08-01" and rain["periodEnd"] == "2026-08-31"  # the last month before the fill
    ((total,),) = fixture_db(
        """SELECT sum(rainfall_mm_day) FROM "8_1_reservoir_operational_master"
           WHERE district_name = 'City of Cape Town' AND date BETWEEN '2026-08-01' AND '2026-08-31'"""
    )
    assert rain["values"]["CPT"] == round(float(total))
    sm = (await client.get(f"{D}/map", params={"layer": "soil-moisture"})).json()
    assert sm["unit"] == "m³/m³" and set(sm["values"]) == {"DC44", "CPT", "DC6"}
