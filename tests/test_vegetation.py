"""Vegetation endpoints on the recorded fixture DB (DC44, CPT, DC6; satellite table complete 2001–2025)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/vegetation"


def kpis(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["id"]: k for k in body["kpis"]}


async def test_summary_from_satellite(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "CPT"})).json()
    c.KpiSet.model_validate(body)
    k = kpis(body)
    ((ndvi, vhi),) = fixture_db(
        "SELECT round(ndvi::numeric, 3), vhi FROM satellite_remote_sensing "
        "WHERE district_name = 'cape town' AND date = '2025-12-01'"
    )
    assert k["ndvi"]["value"] == float(ndvi)
    assert k["stress"]["value"] == (100.0 if vhi < 40 else 0.0)  # one district: all or nothing
    assert body["source"] == ["satellite_remote_sensing"]
    assert body["asOf"] == "2025-12-01" and body["isStale"] is True


async def test_same_value_as_drought_page(client: httpx.AsyncClient) -> None:
    """D4: one source for both pages — the same district-month never shows two values."""
    veg = kpis((await client.get(f"{B}/summary", params={"municipality": "DC44"})).json())["vci"]["value"]
    dro = kpis((await client.get("/api/v1/drought/summary", params={"municipality": "DC44"})).json())["vci"]["value"]
    assert veg == dro


@pytest.mark.parametrize("key", ["indices", "index-trend", "anomaly", "drivers"])
async def test_timeseries(client: httpx.AsyncClient, key: str) -> None:
    r = await client.get(f"{B}/timeseries", params={"metric": key, "municipality": "DC6"})
    assert r.status_code == 200, r.text
    c.SeriesSet.model_validate(r.json())


async def test_indices_only_ndvi_is_live(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/timeseries", params={"metric": "indices", "municipality": "DC6"})).json()
    s = {x["key"]: x for x in body["series"]}
    assert any(p[1] is not None for p in s["ndvi"]["data"])
    for k in ("evi", "savi", "msavi2"):
        assert all(p[1] is None for p in s[k]["data"])
    assert {u["field"] for u in body["unavailable"]} == {"series.evi", "series.savi", "series.msavi2"}


@pytest.mark.parametrize("key", ["classification", "seasonal", "drivers"])
async def test_breakdowns(client: httpx.AsyncClient, key: str) -> None:
    r = await client.get(f"{B}/breakdown", params={"metric": key, "municipality": "DC44"})
    assert r.status_code == 200, r.text
    c.CategorySet.model_validate(r.json())


async def test_drivers_component_dates(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/breakdown", params={"metric": "drivers", "municipality": "DC44"})).json()
    comps = {x["key"]: x for x in body["components"]}
    assert comps["lst"]["asOf"] == "2025-12-01" and comps["lst"]["isStale"] is True
    assert comps["rain_daily"]["asOf"] == "2026-08-01" and comps["rain_daily"]["isStale"] is False


async def test_hotspots(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/table", params={"metric": "hotspots"})).json()
    c.TableSet.model_validate(body)
    vci = [r["vci"] for r in body["rows"]]
    assert vci == sorted(vci) and len(vci) == 3  # lowest VCI first; only the fixture's 3 districts have data


@pytest.mark.parametrize("layer", ["ndvi", "vci", "vhi", "tci", "ndwi", "lst", "sm", "ndvi-anomaly", "ndvi-trend"])
async def test_maps(client: httpx.AsyncClient, layer: str) -> None:
    body = (await client.get(f"{B}/map", params={"layer": layer})).json()
    c.MapLayer.model_validate(body)
    assert set(body["values"]) == {"DC44", "CPT", "DC6"}
    assert body["source"] == ["satellite_remote_sensing"]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        *[("map", {"layer": x}) for x in ("evi", "savi", "msavi2", "vpi", "asi", "svi", "ndii")],
        ("map", {"layer": "forecast-ndvi"}),
        ("map", {"layer": "forecast-anomaly"}),
        ("forecast", {"metric": "ndvi"}),
        ("forecast", {"metric": "recovery-probability"}),
        ("breakdown", {"metric": "land-cover"}),
        ("table", {"metric": "forecast-indicators"}),
    ],
)
async def test_unavailable(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> None:
    r = await client.get(f"{B}/{path}", params=params)
    assert r.status_code == 501 and r.json()["code"] == "DATA_UNAVAILABLE"


async def test_withheld_5_1_never_served(client: httpx.AsyncClient) -> None:
    for kind, key in (("summary", None), ("timeseries", "indices"), ("map", "ndvi")):
        params = {"metric": key} if kind == "timeseries" else ({"layer": key} if kind == "map" else {})
        body = (await client.get(f"{B}/{kind}", params=params)).json()
        assert "5.1_primary_satellite_vegetation_variables_sansa_master" not in body.get("source", [])


async def test_temperature_drivers_change_in_their_own_unit(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    """B5: temperature-type drivers are absolute changes (°C, °C·days), never % of a temperature."""
    body = (await client.get(f"{B}/breakdown", params={"metric": "drivers", "municipality": "DC44"})).json()
    units = dict(zip(body["categories"], body["units"], strict=True))
    assert body["unit"] == "mixed"
    assert units == {
        "Rainfall (%)": "%",
        "Mean temperature (°C)": "°C",
        "Reference ET₀ (%)": "%",
        "Growing degree days (°C·days)": "°C·days",
        "Land surface temperature (°C)": "°C",
    }
    got = dict(zip(body["categories"], body["series"][0]["values"], strict=True))["Mean temperature (°C)"]
    rows = fixture_db(
        """SELECT left("Date", 7) AS m, avg("Tavg_C") FROM "4_1_temperature_change_master"
           WHERE "District_Name" = 'Alfred Nzo' GROUP BY 1"""
    )
    monthly = {m: float(v) for m, v in rows}
    last6 = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]  # latest complete months
    normal = [
        sum(v for m, v in monthly.items() if m[5:] == k[5:] and "2001" <= m[:4] <= "2025")
        / sum(1 for m in monthly if m[5:] == k[5:] and "2001" <= m[:4] <= "2025")
        for k in last6
    ]
    expected = sum(monthly[k] for k in last6) / 6 - sum(normal) / 6
    assert got == pytest.approx(expected, abs=0.006)


async def test_indicator_trends_temperature_in_degrees(client: httpx.AsyncClient) -> None:
    body = (
        await client.get("/api/v1/climate/timeseries", params={"metric": "indicator-trends", "municipality": "CPT"})
    ).json()
    temp = next(s for s in body["series"] if s["key"] == "temp")
    assert temp["axis"] == 1 and body["axes"][1]["unit"] == "°C"
    assert all(s.get("axis", 0) == 0 for s in body["series"] if s["key"] != "temp")


def test_symmetric_legend_breaks_follow_the_data() -> None:
    """B12: breaks at the 40 % / 80 % quantiles of |trend|, symmetric, rounded to 2 significant figures."""
    from app.core.palettes import NDVI_TREND, symmetric_legend

    values = [0.001 * i for i in range(1, 51)]  # |trend| 0.001 … 0.050
    leg = symmetric_legend(values, NDVI_TREND, "/decade", "browning", "greening")
    edges = [(x["from"], x["to"]) for x in leg]
    assert edges == [(None, -0.04), (-0.04, -0.021), (-0.021, 0.021), (0.021, 0.04), (0.04, None)]  # q40 = 0.0206
    assert [x["color"] for x in leg] == NDVI_TREND
    middle = sum(1 for v in values if -0.021 <= v < 0.021)
    assert middle < len(values) / 2  # the fixed ±0.05 class would hold all 50


async def test_ndvi_trend_map_returns_its_legend(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/map", params={"layer": "ndvi-trend"})).json()
    c.MapLayer.model_validate(body)
    leg = body["legend"]
    assert len(leg) == 5 and leg[0]["from"] is None and leg[-1]["to"] is None
    assert leg[1]["from"] == -leg[3]["to"] and leg[2]["from"] == -leg[2]["to"]  # symmetric around 0


async def test_vegetation_classes_use_the_spec_names(client: httpx.AsyncClient) -> None:
    """BACKEND_ISSUES #12: Excellent / Good / Fair / Poor / Very Poor (spec §5.3) wherever a class is sent."""
    spec = {"Excellent", "Good", "Fair", "Poor", "Very Poor"}
    old = ("Very Healthy", "Healthy", "Moderate", "Stressed")
    for path, params in [
        (f"{B}/summary", {"municipality": "DC44"}),
        (f"{B}/breakdown", {"metric": "classification", "municipality": "DC44"}),
        (f"{B}/table", {"metric": "provinces"}),
        (f"{B}/table", {"metric": "hotspots"}),
        ("/api/v1/drought/table", {"metric": "cascade", "municipality": "DC44"}),
        ("/api/v1/drought/summary", {"municipality": "DC44"}),
    ]:
        r = await client.get(path, params=params)
        assert r.status_code == 200 and not any(f'"{w}"' in r.text for w in old), (path, params)
    cls = (await client.get(f"{B}/breakdown", params={"metric": "classification", "municipality": "DC44"})).json()
    assert set(cls["categories"]) <= spec
    cas = (await client.get("/api/v1/drought/table", params={"metric": "cascade", "municipality": "DC44"})).json()
    veg = next(r for r in cas["rows"] if r["stageId"] == "vegetation")
    assert veg["cls"] in spec and veg["class"] == veg["cls"]
