"""API tests against the local fixture DB: health, meta, reference lists, error bodies, CORS, rate limiting."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.registry.registry import load_registry
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
from app.modules import IMPLEMENTED_MODULES  # noqa: E402

REG = load_registry()


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"]["reachable"] is True
    assert body["database"]["readOnlySession"] is True
    assert {m["module"] for m in body["modules"]} == set(REG.modules)
    for m in body["modules"]:
        assert m["status"] in ("live", "partial", "unavailable")
        if m["status"] != "live":
            assert m["reason"]
        assert m["status"] == ("live" if m["module"] in IMPLEMENTED_MODULES else "partial"), m


async def test_sessions_reject_writes(client: httpx.AsyncClient) -> None:
    """The pool's connections are read-only even though the fixture role could write."""
    app_state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="read-only"):
        await app_state.db.fetch("CREATE TEMP TABLE should_fail (x int)")


async def test_meta_sources_match_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    r = await client.get("/api/v1/meta/sources")
    assert r.status_code == 200
    body = r.json()
    items = {i["table"]: i for i in body["items"]}
    assert set(items) == set(REG.datasets)
    for table, item in items.items():
        ds = REG.datasets[table]
        # rows dated after today are never served, so they are not counted either (8_1 runs to 2026-12-31)
        ((n, last),) = fixture_db(f"SELECT count(*), max({ds.date_sql()}) FROM {ds.fq} WHERE {ds.not_future_sql()}")
        assert item["rows"] == n, table
        assert item["asOf"] == last.isoformat(), table
        assert item["error"] is None
    # the newest daily record not after today: 8_1 / 9_1 (added 2026-10-19) run past the rainfall tables' 2026-09-06
    ((newest,),) = fixture_db("SELECT least(CURRENT_DATE, DATE '2026-12-31')")
    assert body["newestDataDate"] == newest.isoformat()
    assert items["satellite_remote_sensing"]["isStale"] is True  # ends 2025-12
    assert items["hydrological_drought_master"]["isStale"] is True
    assert items["6.1_core_rainfall_variables_master"]["isStale"] is False
    withheld = {
        w["column"] for w in items["5.1_primary_satellite_vegetation_variables_sansa_master"]["variables"]["withheld"]
    }
    assert "NDVI_10day_composite" in withheld and "VPI_percent" in withheld


async def test_meta_completeness_formula(client: httpx.AsyncClient) -> None:
    items = {i["table"]: i for i in (await client.get("/api/v1/meta/sources")).json()["items"]}
    sat = items["satellite_remote_sensing"]  # 3 districts × 300 months in the fixture, out of 52 × 300
    assert sat["completenessPct"] == round(100 * 900 / (52 * 300), 2)


async def test_meta_coverage_and_variables(client: httpx.AsyncClient) -> None:
    cov = (await client.get("/api/v1/meta/coverage")).json()
    mods = {m["module"]: m for m in cov["modules"]}
    assert set(mods) == set(REG.modules)
    assert mods["drought"]["isStale"] is True and mods["drought"]["oldestCoveredTo"] == "2025-12-31"
    assert "satellite_remote_sensing" in mods["vegetation"]["tables"]  # D4: one vegetation source for both pages

    var = {v["id"]: v for v in (await client.get("/api/v1/meta/variables")).json()["items"]}
    assert var["ndvi"]["status"] == "live" and var["ndvi"]["source"] == "satellite_remote_sensing.ndvi"
    assert var["evi"]["status"] == "unavailable" and var["evi"]["withheld"]
    assert "meteorological_drought_master.spi_6" in var["spi_6"]["duplicates"]


async def test_reference_lists(client: httpx.AsyncClient) -> None:
    prov = (await client.get("/api/v1/reference/provinces")).json()
    assert prov["type"] == "provinces" and [i["id"] for i in prov["items"]] == sorted(
        ["EC", "FS", "GP", "KZN", "LP", "MP", "NC", "NW", "WC"]
    )
    wmas = (await client.get("/api/v1/reference/wmas")).json()
    assert [i["id"] for i in wmas["items"]] == [f"WMA{i}" for i in range(1, 10)]
    munis = (await client.get("/api/v1/reference/municipalities")).json()
    assert len(munis["items"]) == 52
    cpt = next(i for i in munis["items"] if i["id"] == "CPT")
    assert cpt == {"id": "CPT", "name": "City of Cape Town", "parent": "WC", "province": "WC"}
    assert all(set(i) <= {"id", "name", "parent", "province", "wma"} for i in munis["items"])


async def test_catchment_reference_is_unavailable(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/reference/catchments")
    assert r.status_code == 501
    body = r.json()
    assert body["code"] == "DATA_UNAVAILABLE" and body["error"] and body["detail"] and body["missing"]


@pytest.mark.parametrize("path", ["/api/v1/reference/rivers", "/api/v1/nonexistent/summary", "/api/v1/nope"])
async def test_404_bodies(client: httpx.AsyncClient, path: str) -> None:
    r = await client.get(path)
    assert r.status_code == 404
    assert isinstance(r.json()["error"], str)


async def test_cors(client: httpx.AsyncClient) -> None:
    ok = await client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = await client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


async def test_request_id_header(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/health", headers={"X-Request-Id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


async def test_rate_limit(client_factory: Callable[..., Any]) -> None:
    async for c in client_factory(rate_limit_per_minute=2):
        codes = [(await c.get("/api/v1/reference/provinces")).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        r = await c.get("/api/v1/reference/provinces")
        assert r.json()["code"] == "RATE_LIMITED" and int(r.headers["retry-after"]) >= 1


async def test_no_database_configured(client_factory: Callable[..., Any]) -> None:
    async for c in client_factory(supabase_db_url=None):
        body = (await c.get("/api/v1/health")).json()
        assert body["status"] == "down" and body["database"]["reachable"] is False
        assert all(m["status"] == "unavailable" for m in body["modules"])


async def test_responses_contain_no_secrets(client: httpx.AsyncClient) -> None:
    for path in ("/api/v1/health", "/api/v1/meta/sources", "/api/v1/meta/coverage"):
        text = (await client.get(path)).text
        assert "postgres://" not in text and "postgresql://" not in text and "eyJ" not in text
