"""Drought endpoints on the recorded fixture DB (districts DC44, CPT, DC6)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/drought"


def kpis(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["id"]: k for k in body["kpis"]}


async def test_summary_per_component_dates(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    c.KpiSet.model_validate(body)
    k = kpis(body)
    assert k["spi6"]["asOf"] == "2026-08-01" and k["spi6"]["isStale"] is False
    for kid in ("vci", "soilMoisture"):  # satellite ends 2025-12
        assert k[kid]["asOf"] == "2025-12-01" and k[kid]["isStale"] is True, kid
    assert body["isStale"] is True  # at least one component is stale
    comps = {x["key"]: x for x in body["components"]}
    assert comps["vci"]["source"] == ["satellite_remote_sensing"]
    assert comps["spi6"]["source"] == ["6.2_rainfall_derived_drought_indices_master"]
    assert k["class"]["status"]["scale"] == "drought"
    for kid in ("ssi3", "settlements", "storage", "sgi"):
        assert k[kid]["value"] is None and k[kid]["unavailableReason"]
    assert {u["field"] for u in body["unavailable"]} == {"kpis.ssi3", "kpis.settlements", "kpis.storage", "kpis.sgi"}


async def test_vci_matches_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "CPT"})).json()
    ((v,),) = fixture_db(
        "SELECT round(vci::numeric, 1) FROM satellite_remote_sensing "
        "WHERE district_name = 'cape town' AND date = '2025-12-01'"  # satellite alias for City of Cape Town
    )
    assert kpis(body)["vci"]["value"] == float(v)


async def test_dam_storage_withheld_after_plausibility_check(client: httpx.AsyncClient) -> None:
    """Reservoir columns failed the 2026-10-15 plausibility check (DATA_GAPS §15): never served, always explained."""
    body = (await client.get(f"{B}/summary", params={"municipality": "DC44"})).json()
    storage = kpis(body)["storage"]
    assert storage["value"] is None and "plausibility" in storage["unavailableReason"]
    assert "hydrological_drought_master" not in body["source"]  # storage and SGI are both withheld
    r = await client.get(f"{B}/timeseries", params={"metric": "dam-flows", "municipality": "DC44"})
    assert r.status_code == 501 and "plausibility" in r.json()["error"]


@pytest.mark.parametrize(
    ("kind", "key", "model"),
    [
        ("timeseries", "spi", c.SeriesSet),
        ("timeseries", "rain-spei", c.SeriesSet),
        ("timeseries", "rain-anomaly", c.SeriesSet),
        ("timeseries", "soil-moisture", c.SeriesSet),
        ("timeseries", "vegetation", c.SeriesSet),
        ("timeseries", "drought-area", c.SeriesSet),
        ("breakdown", "severity", c.CategorySet),
        ("table", "cascade", c.TableSet),
        ("table", "municipalities", c.TableSet),
        ("table", "sources", c.TableSet),
    ],
)
async def test_datasets_validate(client: httpx.AsyncClient, kind: str, key: str, model: type[c.ContractModel]) -> None:
    r = await client.get(f"{B}/{kind}", params={"metric": key, "municipality": "DC44"})
    assert r.status_code == 200, r.text
    model.model_validate(r.json())


async def test_rain_anomaly_reuses_rainfall(client: httpx.AsyncClient) -> None:
    d = (await client.get(f"{B}/timeseries", params={"metric": "rain-anomaly", "municipality": "DC6"})).json()
    r = (await client.get("/api/v1/rainfall/timeseries", params={"metric": "anomaly", "municipality": "DC6"})).json()
    assert d["id"] == "rain-anomaly" and d["series"] == r["series"]


async def test_cascade_components(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/table", params={"metric": "cascade", "municipality": "DC6"})).json()
    rows = {r["id"]: r for r in body["rows"]}
    assert rows["meteorological"]["asOf"] == "2026-08-01"
    assert rows["agricultural"]["asOf"] == "2025-12-01" and rows["hydrological"]["asOf"] is None
    assert rows["socio-economic"]["value"] is None and rows["hydrological"]["value"] is None
    stale = {x["key"]: x["isStale"] for x in body["components"]}
    assert stale == {"meteorological": False, "soil-moisture": True, "agricultural": True}


async def test_gw_sgi_unavailable(client: httpx.AsyncClient) -> None:
    """Borehole level (not a depth) and SGI (frozen since Sep 2024) are both withheld: the chart is 501."""
    r = await client.get(f"{B}/timeseries", params={"metric": "gw-sgi", "municipality": "DC6"})
    assert r.status_code == 501 and "frozen" in r.json()["error"]


async def test_vegetation_same_source_as_vegetation_page(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/timeseries", params={"metric": "vegetation", "municipality": "CPT"})).json()
    assert body["source"] == ["satellite_remote_sensing"]


@pytest.mark.parametrize(
    ("layer", "level", "scale"),
    [
        ("drought-class", "municipality", "spi"),
        ("municipal-class", "municipality", "drought"),
        ("spi24", "municipality", "spi"),
        ("spei12", "municipality", "spi"),
        ("rain-anomaly", "municipality", "anomalyPct"),
        ("soil-moisture", "municipality", "soilMoisture"),
        ("ndvi", "municipality", "ndvi"),
        ("vci", "municipality", "vci"),
    ],
)
async def test_maps(client: httpx.AsyncClient, layer: str, level: str, scale: str) -> None:
    body = (await client.get(f"{B}/map", params={"layer": layer, "province": "NC"})).json()
    c.MapLayer.model_validate(body)
    assert (body["level"], body["scale"]) == (level, scale)
    assert set(body["values"]) == {"DC6"}


async def test_municipal_class_values_are_class_ids(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{B}/map", params={"layer": "municipal-class"})).json()
    assert set(body["values"].values()) <= {"None", "D0", "D1", "D2", "D3", "D4"}


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("timeseries", {"metric": "storage-vs-spi"}),
        ("timeseries", {"metric": "population-at-risk"}),
        ("timeseries", {"metric": "recovery-province"}),
        ("forecast", {"metric": "spi"}),
        ("forecast", {"metric": "settlements"}),
        ("breakdown", {"metric": "restrictions"}),
        ("breakdown", {"metric": "class-probability"}),
        ("table", {"metric": "early-warning"}),
        ("table", {"metric": "forecast-kpis"}),
        ("matrix", {"metric": "province-risk"}),
        ("map", {"layer": "catchment-class"}),
        ("map", {"layer": "dams"}),
        ("map", {"layer": "boreholes"}),
        ("map", {"layer": "settlements"}),
        ("map", {"layer": "forecast-6"}),
    ],
)
async def test_unavailable(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> None:
    r = await client.get(f"{B}/{path}", params=params)
    assert r.status_code == 501, r.text
    assert r.json()["code"] == "DATA_UNAVAILABLE" and r.json()["missing"]


@pytest.mark.parametrize(
    ("path", "params"),
    [("timeseries", {"metric": "x"}), ("map", {"layer": "x"}), ("matrix", {"metric": "x"}), ("table", {"metric": "x"})],
)
async def test_unknown_keys(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> None:
    assert (await client.get(f"{B}/{path}", params=params)).status_code == 404


async def test_no_entities_route(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{B}/entities")).status_code == 404  # the contract defines no drought entities


async def test_ssi_is_the_same_rule_on_both_pages(client: httpx.AsyncClient) -> None:
    """B8: SSI comes from the modelled runoff depth on the drought and runoff pages; the DB's SSI column is withheld."""
    for scope in ({"municipality": "DC44"}, {"municipality": "DC6"}):
        d = (await client.get(f"{B}/summary", params=scope)).json()
        k = next(x for x in d["kpis"] if x["id"] == "ssi3")
        assert k["label"] == "SSI-3 (modelled runoff)"
        ts = (await client.get("/api/v1/runoff/timeseries", params={"metric": "ssi", **scope})).json()
        s3 = next(s for s in ts["series"] if s["key"] == "ssi3")
        assert s3["name"] == "SSI-3 (modelled runoff)" and ts["title"] == "SSI (modelled runoff)"
        last = dict(map(tuple, s3["data"])).get(k.get("asOf"))
        assert k["value"] == last
        if k["value"] is None:  # the fixture holds < 10 calibration years: same reason on both pages
            assert "calibration" in k["unavailableReason"]


async def test_soil_moisture_fill_before_2015_is_never_read(client: httpx.AsyncClient) -> None:
    """Satellite soil moisture is one constant per district until Feb 2015 (registry `valid_from`): never served."""
    import pandas as pd

    state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    for var in ("soil_moisture_surface", "soil_moisture_rootzone"):  # one loader for every page
        df = await state.data.monthly(var)
        assert df.empty or df["date"].min() >= pd.Timestamp("2015-03-01"), var
    ds = state.registry.datasets["satellite_remote_sensing"]
    assert ds.valid_sql("surface_soil_moisture") == "\"date\"::date >= DATE '2015-03-01'"
    assert ds.valid_sql("ndvi") == "TRUE"
