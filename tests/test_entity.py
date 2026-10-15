"""`entityId` (B1) on the recorded fixture DB: every entity dataset follows the selected district, whatever the
page scope. Runoff used to ignore it and return the scope aggregate (national, or another area with `province=WC`)."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import requires_db
from tests.entity_audit import check, entity_endpoints

pytestmark = [pytest.mark.db, requires_db]
SCOPES = [{}, {"province": "WC"}, {"municipality": "CPT"}, {"province": "EC"}]


def test_every_entity_endpoint_is_enumerated() -> None:
    paths = {(p, tuple(sorted(q.items()))) for p, q in entity_endpoints()}
    for metric in ("discharge", "fdc", "flood-frequency", "seasonal"):
        assert ("/api/v1/runoff/timeseries", (("metric", metric),)) in paths
    assert ("/api/v1/runoff/table", (("metric", "statistics"),)) in paths
    assert ("/api/v1/rainfall/timeseries", (("metric", "station"),)) in paths
    assert sum(1 for p, _ in paths if "/category/" in p) == 5


async def test_entity_datasets_follow_the_district(client: httpx.AsyncClient) -> None:
    problems, compared = await check(client, "DC44", "DC6", SCOPES)
    assert not problems, "\n".join(problems)
    assert compared >= 9, compared  # flood frequency needs 10 years: 501 for both on the 3-year fixture


async def test_runoff_discharge_is_the_districts_flow(client: httpx.AsyncClient) -> None:
    """The reported case: `entityId=DC44` used to return the national aggregate."""
    p = {"metric": "discharge"}
    ent = (await client.get("/api/v1/runoff/timeseries", params={**p, "entityId": "DC44"})).json()
    muni = (await client.get("/api/v1/runoff/timeseries", params={**p, "municipality": "DC44"})).json()
    assert ent["series"] == muni["series"] and ent["aggregation"] == "district"
    assert ent["title"].endswith("Alfred Nzo")
    stats = (await client.get("/api/v1/runoff/table", params={"metric": "statistics", "entityId": "DC44"})).json()
    muni_s = (await client.get("/api/v1/runoff/table", params={"metric": "statistics", "municipality": "DC44"})).json()
    assert stats["rows"] == muni_s["rows"]


async def test_unknown_entity_is_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/runoff/timeseries", params={"metric": "discharge", "entityId": "XX1"})
    assert r.status_code == 404 and r.json()["issues"][0]["param"] == "entityId"
