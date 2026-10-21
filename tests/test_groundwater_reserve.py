"""Groundwater Reserve Assessment (spec §11): recharge, abstraction, exploitation index classes, reserves; vs SQL."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.spatial import get_spatial
from app.core.thresholds import exploitation_class
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
B = "/api/v1/groundwater-reserve"


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


async def test_recharge_is_direct_times_area_plus_indirect(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    ((direct, indirect, abst, ei),) = fixture_db(
        """SELECT max(r."Direct recharge"), max(r."Indirect recharge"), max(s."Total abstraction"),
                  max(s."Exploitation index")
           FROM "11.2_recharge_and_discharge" r JOIN "11.3_aquifer_sustainability" s USING (district_name, year)
           WHERE district_name = 'Namakwa' AND year = 2025"""
    )
    area = (get_spatial().district_areas() or {})["DC6"]
    assert kpi(body, "recharge")["value"] == round(float(direct) * area / 1000 + float(indirect) / 1e6)
    assert kpi(body, "abstraction")["value"] == round(float(abst) / 1e6)
    k = kpi(body, "ei")
    assert k["value"] == round(float(ei), 1) and k["status"]["id"] == exploitation_class(float(ei) / 100)


async def test_reserves_are_the_stored_11_1_values(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "CPT"})).json()
    ((static, dynamic),) = fixture_db(
        """SELECT max("Static reserve volume"), max("Dynamic (renewable) reserve") FROM "11_1_hydrogeological"
           WHERE district_name = 'City of Cape Town' AND year = 2025"""
    )
    assert kpi(body, "static")["value"] == round(float(static))
    assert kpi(body, "dynamic")["value"] == round(float(dynamic))


async def test_ei_classes_and_map(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    rows = fixture_db(
        """SELECT district_name, max("Exploitation index") FROM "11.3_aquifer_sustainability" WHERE year = 2025
           GROUP BY 1"""
    )
    bd = (await client.get(f"{B}/breakdown", params={"metric": "ei-classes"})).json()
    counts = dict(zip(bd["categories"], bd["series"][0]["values"], strict=True))
    for _, v in rows:
        assert counts[exploitation_class(float(v) / 100)] >= 1
    assert sum(counts.values()) == 3
    m = (await client.get(f"{B}/map", params={"layer": "ei"})).json()
    assert m["scale"] == "exploitation" and all(0 <= v < 2 for v in m["values"].values())  # a ratio, not %


async def test_components_say_the_ecological_reserve_is_missing(client: httpx.AsyncClient) -> None:
    bd = (await client.get(f"{B}/breakdown", params={"metric": "components", "municipality": "DC6"})).json()
    vals = dict(zip(bd["categories"], bd["series"][0]["values"], strict=True))
    assert vals["Dynamic reserve"] is not None and vals["Ecological reserve (not in the database)"] is None


async def test_unavailable_and_reused(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{B}/timeseries", params={"metric": "yield-ei"})).status_code == 501
    sgi = (await client.get(f"{B}/map", params={"layer": "sgi"})).json()
    assert sgi["isStale"] is True
    card = (await client.get(f"{B}/entities/DC6")).json()
    assert card["image"] == "aquifer" and any(f["label"].startswith("Transmissivity") for f in card["fields"])
