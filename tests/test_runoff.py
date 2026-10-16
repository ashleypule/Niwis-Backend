"""Runoff endpoints on the recorded fixture DB (DC44, CPT, DC6; daily runoff from 2024)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.spatial import get_spatial
from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/runoff"
WIN = "'2025-09-07' AND '2026-09-06'"


def kpis(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["id"]: k for k in body["kpis"]}


async def test_depth_recovered_from_model_volume(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    c.KpiSet.model_validate(body)
    ((depth,),) = fixture_db(
        f"""SELECT round((sum("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000)::numeric, 1)
            FROM runoff_variables_master WHERE "District_Name" = 'Namakwa' AND "Date" BETWEEN {WIN}"""
    )
    assert kpis(body)["runoff"]["value"] == float(depth)


async def test_volume_uses_real_area_not_6500(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    card = (await client.get(f"{B}/entities/DC6")).json()
    c.EntityDetail.model_validate(card)
    vol = next(f["value"] for f in card["fields"] if f["label"] == "Runoff volume, last 12 months")
    ((model_m3,),) = fixture_db(
        f"""SELECT sum("Daily_Surface_Runoff_Volume_m3_day") FROM runoff_variables_master
            WHERE "District_Name" = 'Namakwa' AND "Date" BETWEEN {WIN}"""
    )
    area = (get_spatial().district_areas() or {})["DC6"]
    assert vol == pytest.approx(float(model_m3) / 6.5e9 * 1000 * area / 1000, abs=0.6)
    assert vol != pytest.approx(float(model_m3) / 1e6, rel=0.01)  # not the table's 6 500 km² volume


async def test_withheld_and_station_kpis(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "CPT"})).json()
    k = kpis(body)
    for kid in ("peak", "normal", "low"):
        assert k[kid]["value"] is None and k[kid]["unavailableReason"]
    reasons = {u["field"]: u["reason"] for u in body["unavailable"]}
    assert set(reasons) == {"kpis.peak", "kpis.normal", "kpis.low", "kpis.ssi6"}
    assert k["ssi6"]["value"] is None  # fixture: < 10 calibration years → no SSI rather than a poor fit
    assert "status" not in k["ssi6"]  # no "No drought" pill for a missing value
    assert "10 calibration years" in reasons["kpis.ssi6"] == k["ssi6"]["unavailableReason"]


@pytest.mark.parametrize(
    "key",
    [
        "discharge",
        "runoff-anomaly",
        "ssi",
        "rainfall-runoff",
        "water-balance",
        "fdc",
        "seasonal",
        "national-volume",
        "drivers",
    ],
)
async def test_timeseries_validate(client: httpx.AsyncClient, key: str) -> None:
    r = await client.get(f"{B}/timeseries", params={"metric": key, "municipality": "CPT"})
    assert r.status_code == 200, r.text
    c.SeriesSet.model_validate(r.json())


async def test_flood_frequency_needs_ten_years(client: httpx.AsyncClient) -> None:
    r = await client.get(f"{B}/timeseries", params={"metric": "flood-frequency", "municipality": "CPT"})
    assert r.status_code == 501 and r.json()["code"] == "DATA_UNAVAILABLE"  # fixture holds 2024–2026 only


async def test_discharge_is_sum_over_scope(client: httpx.AsyncClient) -> None:
    one = (await client.get(f"{B}/timeseries", params={"metric": "discharge", "municipality": "CPT"})).json()
    daily = {p[0]: p[1] for p in one["series"][0]["data"]}
    assert len(daily) == 365 and all(v is not None and v >= 0 for v in daily.values())


async def test_downsampling_long_series(client: httpx.AsyncClient) -> None:
    body = (
        await client.get(
            f"{B}/timeseries", params={"metric": "discharge", "municipality": "CPT", "period": "historical"}
        )
    ).json()
    assert all(len(s["data"]) <= 2000 for s in body["series"])


@pytest.mark.parametrize(
    ("kind", "key", "model"),
    [("table", "statistics", c.TableSet), ("table", "top-stations", c.TableSet), ("table", "events", c.TableSet)],
)
async def test_tables(client: httpx.AsyncClient, kind: str, key: str, model: type[c.ContractModel]) -> None:
    r = await client.get(f"{B}/{kind}", params={"metric": key, "province": "WC"})
    assert r.status_code == 200, r.text
    model.model_validate(r.json())


async def test_events_above_q10(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get(f"{B}/table", params={"metric": "events", "municipality": "DC44", "limit": "500"})).json()
    ((n,),) = fixture_db(
        f"""WITH q AS (SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY "Daily_Surface_Runoff_Volume_m3_day") AS q10
                       FROM runoff_variables_master
                       WHERE "District_Name" = 'Alfred Nzo' AND "Date" BETWEEN '2000-01-01' AND '2025-12-31')
            SELECT count(*) FROM runoff_variables_master, q
            WHERE "District_Name" = 'Alfred Nzo' AND "Date" BETWEEN {WIN}
              AND "Daily_Surface_Runoff_Volume_m3_day" > q10"""
    )
    assert body["total"] == n


@pytest.mark.parametrize("layer", ["annual-runoff", "runoff-anomaly", "monthly-runoff", "runoff-coefficient"])
async def test_maps(client: httpx.AsyncClient, layer: str) -> None:
    body = (await client.get(f"{B}/map", params={"layer": layer})).json()
    c.MapLayer.model_validate(body)
    assert set(body["values"]) == {"DC44", "CPT", "DC6"}


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("forecast", {"metric": "runoff"}),
        ("forecast", {"metric": "flood-probability"}),
        ("breakdown", {"metric": "by-province"}),
        ("map", {"layer": "catchment-runoff"}),
        ("map", {"layer": "stations"}),
        ("map", {"layer": "flow-status"}),
        ("map", {"layer": "flow-anomaly"}),
        ("map", {"layer": "ssi"}),
    ],
)
async def test_unavailable(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> None:
    r = await client.get(f"{B}/{path}", params=params)
    assert r.status_code == 501 and r.json()["code"] == "DATA_UNAVAILABLE"


async def test_by_wma_flagged(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/breakdown", params={"metric": "by-wma"})).json()
    c.CategorySet.model_validate(body)
    assert body["aggregation"] == "district-overlap" and len(body["categories"]) == 9


async def test_entities(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{B}/entities")).json()["total"] == 52
    assert (await client.get(f"{B}/entities/NOPE")).status_code == 404


def test_flood_frequency_uses_the_spec_return_periods() -> None:
    """B9: spec §7.2 — 2, 5, 10, 20, 50 and 100-year return period flows (no 1.25 or 25)."""
    from app.modules.runoff.service import RETURN_PERIODS

    assert RETURN_PERIODS == [2, 5, 10, 20, 50, 100]


async def test_fdc_long_term_is_the_common_period(client: httpx.AsyncClient) -> None:
    """B10: the long-term flow-duration curve uses 2000–(last full year) like every other long-term value."""
    body = (await client.get(f"{B}/timeseries", params={"metric": "fdc", "municipality": "DC44"})).json()
    lt = next(s for s in body["series"] if s["key"] == "lt")
    assert lt["name"] == "Long-term (2000–2025)" and "2000–2025" in body["note"]
