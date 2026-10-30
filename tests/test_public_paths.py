"""A production frontend talks only to the API: every contract path answers with data or a specific 501, never a bare
404; search and local municipalities come from the reference files."""

from __future__ import annotations

import httpx
import pytest

from app.modules import DATABASE_VIEW_MODULES, IMPLEMENTED_MODULES
from app.modules.unavailable import CONTRACT_MODULES, KINDS, REASONS
from app.refdata.places import local_municipalities
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


async def test_modules_without_tables_answer_501_with_a_reason(client: httpx.AsyncClient) -> None:
    for module in sorted(set(CONTRACT_MODULES) - IMPLEMENTED_MODULES):
        for kind in ("summary", "map", "timeseries", "table", "breakdown", "entities"):
            r = await client.get(f"/api/v1/{module}/{kind}", params={"metric": "x", "layer": "x"})
            if module == "ai" and kind == "summary":  # the AI page's KPIs are served (DECISIONS D47)
                assert r.status_code == 200 and r.json()["module"] == "ai"
                continue
            assert r.status_code == 501, (module, kind)
            body = r.json()
            assert body["code"] == "DATA_UNAVAILABLE" and body["detail"] == REASONS[module].replace("{module}", module)
    r = await client.get("/api/v1/alerts", params={"limit": 50})  # derived alerts (DECISIONS D44)
    assert r.status_code == 200 and r.json()["total"] == len(r.json()["items"])


async def test_every_module_has_a_reason_and_database_views_keep_their_categories(client: httpx.AsyncClient) -> None:
    assert set(REASONS) == set(CONTRACT_MODULES) - IMPLEMENTED_MODULES
    for module in sorted(DATABASE_VIEW_MODULES - IMPLEMENTED_MODULES):
        assert (await client.get(f"/api/v1/{module}/categories")).status_code == 200, module
        assert f"/{module}/categories" in REASONS[module].replace("{module}", module)


async def test_unknown_paths_stay_404(client: httpx.AsyncClient) -> None:
    for path in ("/api/v1/nope/summary", "/api/v1/rainfall/bogus", "/api/v1/settlements/bogus", "/api/v1/x"):
        assert (await client.get(path)).status_code == 404, path
    assert "matrix" in KINDS and "reports" not in CONTRACT_MODULES


async def test_live_modules_are_untouched(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/rainfall/summary")).status_code == 200


async def test_local_municipalities_roll_up_to_districts(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/reference/local-municipalities")).json()
    items = {i["id"]: i for i in body["items"]}
    assert len(items) == 213 and items["LM113"]["parent"] == "DC32"  # Mbombela → Ehlanzeni
    districts = {i["id"] for i in (await client.get("/api/v1/reference/municipalities")).json()["items"]}
    assert {i["parent"] for i in items.values()} == districts
    assert len(local_municipalities()) == 213


async def test_search_places(client: httpx.AsyncClient) -> None:
    hits = (await client.get("/api/v1/search", params={"q": "mbombela"})).json()["hits"]
    assert hits[0] == {
        "type": "municipality",
        "id": "LM113",
        "label": "Mbombela",
        "sublabel": "Local municipality, Mpumalanga",
        "path": "/",
        "params": {"province": "MP", "district": "DC32", "municipality": "LM113"},
    }
    district = (await client.get("/api/v1/search", params={"q": "Ehlanzeni"})).json()["hits"][0]
    assert district["type"] == "district" and district["params"] == {"province": "MP", "district": "DC32"}
    assert (await client.get("/api/v1/search", params={"q": "e"})).json()["hits"] == []
    assert (await client.get("/api/v1/search", params={"q": "x" * 101})).status_code == 400
