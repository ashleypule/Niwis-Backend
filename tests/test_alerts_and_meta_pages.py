"""Derived alerts (D44) and the data-integration / governance pages served from the meta facts (D45), on the
recorded fixture DB."""

from __future__ import annotations

import httpx
import pytest

from app.core import districts
from app.core.thresholds import drought_class
from app.registry.registry import load_registry
from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
N_TABLES = len(load_registry().datasets)  # every registered table (44 on 2026-10-29)


async def test_alert_list_is_derived_from_thresholds(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/alerts")
    assert r.status_code == 200, r.text
    body = r.json()
    c.AlertList.model_validate(body)
    assert body["total"] == len(body["items"]) == body["unread"]
    for a in body["items"]:
        assert a["status"] == "open" and a["severity"] in ("critical", "high", "moderate")
        if a["module"] in ("services", "tariffs", "iwrm"):  # their fixtures hold every district (D46)
            continue
        assert a["entityId"] in ("DC44", "CPT", "DC6") and a["province"] in ("EC", "WC", "NC")
    # the drought rule agrees with the drought page's SPI-6 classes
    spi6 = (await client.get("/api/v1/drought/map", params={"layer": "spi6"})).json()
    expected = {
        code for code, v in spi6["values"].items() if v is not None and drought_class(v) in ("D1", "D2", "D3", "D4")
    }
    got = {a["entityId"] for a in body["items"] if a["module"] == "drought"}
    assert got == expected
    # filters
    only = (await client.get("/api/v1/alerts", params={"module": "drought"})).json()
    assert {a["module"] for a in only["items"]} <= {"drought"}
    wc = (await client.get("/api/v1/alerts", params={"province": "WC"})).json()
    assert {a["province"] for a in wc["items"]} <= {"WC"}
    assert (await client.get("/api/v1/alerts", params={"severity": "nope"})).status_code == 400


async def test_alert_summary_and_map(client: httpx.AsyncClient) -> None:
    s = (await client.get("/api/v1/alerts/summary")).json()
    c.KpiSet.model_validate(s)
    ids = [k["id"] for k in s["kpis"]]
    assert ids == ["active", "critical", "high", "open", "modules", "provinces"]
    total = (await client.get("/api/v1/alerts")).json()["total"]
    assert s["kpis"][0]["value"] == total == s["kpis"][3]["value"]
    m = (await client.get("/api/v1/alerts/map", params={"layer": "locations", "province": "EC"})).json()
    c.MapLayer.model_validate(m)
    assert m["scale"] == "severity" and "DC44" in m["values"]
    assert all(districts.by_code(code).province == "EC" for code in m["values"])  # type: ignore[union-attr]
    assert (await client.get("/api/v1/alerts/map", params={"layer": "nope"})).status_code == 404
    assert (await client.get("/api/v1/alerts/timeseries", params={"metric": "x"})).status_code == 404


async def test_data_integration_page(client: httpx.AsyncClient) -> None:
    B = "/api/v1/data-integration"
    s = (await client.get(f"{B}/summary")).json()
    c.KpiSet.model_validate(s)
    k = {x["id"]: x for x in s["kpis"]}
    assert k["feeds"]["value"] == N_TABLES and k["domains"]["value"] >= 14 and k["records"]["value"] > 0
    assert k["providers"]["value"] == 2 and "SANSA, SAWS" in k["providers"]["sublabel"]
    assert 0 <= k["quality"]["value"] <= 100
    st = (await client.get(f"{B}/breakdown", params={"metric": "source-status"})).json()
    assert (
        st["categories"] == ["Live", "Stale", "Withheld only", "Missing"] and sum(st["series"][0]["values"]) == N_TABLES
    )
    bt = (await client.get(f"{B}/breakdown", params={"metric": "by-type"})).json()
    assert len(bt["categories"]) == 3
    p = (await client.get(f"{B}/table", params={"metric": "pipelines"})).json()
    assert len(p["rows"]) == N_TABLES and {r["status"] for r in p["rows"]} <= {"live", "stale", "withheld", "missing"}
    q = (await client.get(f"{B}/table", params={"metric": "quality"})).json()
    assert [r["id"] for r in q["rows"]] == ["completeness", "validity", "timeliness", "coverage"]
    # D47: records per month by source group, the providers named in the tables, the districts each table covers
    ing = await client.get(f"{B}/timeseries", params={"metric": "ingestion"})
    assert ing.status_code == 200, ing.text
    c.SeriesSet.model_validate(ing.json())
    series = {s["key"]: s for s in ing.json()["series"]}
    assert set(series) == {"sansa", "saws", "municipal", "other"} and len(series["other"]["data"]) == 24
    assert sum(v for _, v in series["other"]["data"]) > 0 and "no load log" in ing.json()["note"]
    pv = await client.get(f"{B}/table", params={"metric": "providers"})
    assert pv.status_code == 200, pv.text
    rows = {r["provider"]: r for r in pv.json()["rows"]}
    assert (
        rows["SANSA"]["feeds"] == 4
        and rows["SAWS"]["feeds"] == 2
        and rows["SANSA"]["type"].startswith("Earth observation")
    )
    assert sum(r["feeds"] for r in rows.values()) == N_TABLES and list(rows)[-1].startswith("Not named")
    assert any(u["field"] == "rows.protocol" for u in pv.json()["unavailable"])
    cov = await client.get(f"{B}/map", params={"layer": "coverage"})
    assert cov.status_code == 200, cov.text
    c.MapLayer.model_validate(cov.json())
    vals = cov.json()["values"]
    # the fixture holds three districts in most tables and every district in the §16 / §18 / §19 ones
    assert len(vals) == 52 and vals["CPT"] > 90 and 0 < vals["DC1"] < 30
    wc = (await client.get(f"{B}/map", params={"layer": "coverage", "province": "WC"})).json()
    assert "CPT" in wc["values"] and "DC44" not in wc["values"]


async def test_governance_page(client: httpx.AsyncClient) -> None:
    B = "/api/v1/governance"
    s = (await client.get(f"{B}/summary")).json()
    k = {x["id"]: x for x in s["kpis"]}
    assert k["datasets"]["value"] == N_TABLES and k["metadata"]["value"] == N_TABLES and k["incidents"]["value"] > 100
    inv = (await client.get(f"{B}/breakdown", params={"metric": "inventory"})).json()
    assert sum(inv["series"][0]["values"]) == N_TABLES
    fr = (await client.get(f"{B}/breakdown", params={"metric": "frequency"})).json()
    assert fr["categories"] == ["Daily", "Monthly", "Annual"] and sum(fr["series"][0]["values"]) == N_TABLES
    md = (await client.get(f"{B}/breakdown", params={"metric": "metadata"})).json()
    assert md["series"][0]["values"][1] == 100  # every table has its spec section
    issues = (await client.get(f"{B}/table", params={"metric": "issues", "limit": 5})).json()
    assert len(issues["rows"]) == 5 and issues["total"] > 100 and issues["rows"][0]["reason"]
    assert any(u["field"] == "rows.provider" for u in issues["unavailable"])
    assert k["providers"]["value"] == 2
    pv = (await client.get(f"{B}/table", params={"metric": "providers"})).json()
    assert {r["provider"] for r in pv["rows"]} >= {"SANSA", "SAWS"}
    ret = (await client.get(f"{B}/table", params={"metric": "retention"})).json()
    assert ret["rows"][0]["dataClass"] == "Real-time data" and len(ret["rows"]) == 5
    assert "§23.3" in ret["title"] and "cannot be verified" in ret["title"]
    for kind, key in (("breakdown", "standards"), ("table", "usage"), ("map", "providers")):
        r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
        assert r.status_code == 501, (kind, key)


async def test_alert_rules_of_the_newer_modules(client: httpx.AsyncClient) -> None:
    """Services NRW, RWQO compliance, IWRM losses and tariff increases raise alerts from the same values their pages
    show (D47)."""
    items = (await client.get("/api/v1/alerts")).json()["items"]
    by_module: dict[str, set[str]] = {}
    for a in items:
        by_module.setdefault(a["module"], set()).add(a["entityId"])
    assert set(by_module) <= {
        "drought",
        "vegetation",
        "water-quality",
        "groundwater-reserve",
        "green-drop",
        "blue-drop",
        "services",
        "rwqo",
        "iwrm",
        "tariffs",
    }
    nrw = (await client.get("/api/v1/services/table", params={"metric": "nrw", "limit": 100})).json()["rows"]
    assert by_module.get("services", set()) == {r["id"] for r in nrw if r["status"] == "Above target"}
    inc = (await client.get("/api/v1/tariffs/map", params={"layer": "increase"})).json()["values"]
    assert by_module.get("tariffs", set()) == {k for k, v in inc.items() if v is not None and v > 15}
    for a in items:
        if a["module"] in ("services", "rwqo", "iwrm", "tariffs"):
            assert a["severity"] in ("high", "moderate") and " in " in a["message"]
