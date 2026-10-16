"""BACKEND_ISSUES #11: summary series that plot one stored column name it (`source: ["table.column"]`), so the frontend
can label them from the catalogue; derived series (sums, anomalies, shares) do not."""

from __future__ import annotations

import httpx
import pytest

from app.modules import catalog
from app.registry.catalogue import get_catalogue
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


async def _series(client: httpx.AsyncClient, path: str, **params: str) -> dict[str, dict[str, object]]:
    body = (await client.get(path, params={"municipality": "DC44", **params})).json()
    return {s["key"]: s for s in body["series"]}


async def test_reported_series_name_their_column(client: httpx.AsyncClient) -> None:
    sm = await _series(client, "/api/v1/drought/timeseries", metric="soil-moisture")
    assert sm["surface"]["source"] == ["satellite_remote_sensing.surface_soil_moisture"]
    assert sm["rootzone"]["source"] == ["satellite_remote_sensing.rootzone_soil_moisture"]
    assert "source" not in sm["range"] and "source" not in sm["mean"]  # derived
    spi = await _series(client, "/api/v1/rainfall/timeseries", metric="spi-spei")
    t62 = "6.2_rainfall_derived_drought_indices_master"
    assert spi["spi3"]["source"] == [f"{t62}.SPI_3_Short_Term_Agricultural_Drought"]
    assert spi["spei6"]["source"] == ["meteorological_drought_master.spei_6"]
    veg = await _series(client, "/api/v1/vegetation/timeseries", metric="indices", set="ndvi,vci,evi")
    assert veg["ndvi"]["source"] == ["satellite_remote_sensing.ndvi"]
    assert "source" not in veg["evi"]  # withheld index: no column served


async def test_every_named_column_is_served(client: httpx.AsyncClient) -> None:
    served = {f"{c.table}.{x.column}" for c in get_catalogue().categories for x in c.columns if x.status != "withheld"}
    named = set()
    for path, params in await catalog.requests(client, [{"municipality": "DC44"}]):
        if "/category/" in path or not path.endswith("/timeseries"):
            continue
        r = await catalog.fetch(client, path, params)
        if r.status_code == 200:
            for s in r.json()["series"]:
                named |= set(s.get("source") or [])
    assert named and named <= served, sorted(named - served)
    assert len(named) >= 20, sorted(named)
