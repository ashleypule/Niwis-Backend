"""Water services and tariffs endpoints on the recorded fixture DB (18.x / 19.x: every local municipality, aggregated
to districts; the entities are the municipalities; DECISIONS D46)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


async def test_services_summary_sums_the_municipalities(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    r = await client.get("/api/v1/services/summary", params={"municipality": "CPT"})
    assert r.status_code == 200, r.text
    body = r.json()
    c.KpiSet.model_validate(body)
    served, piped = kpi(body, "served"), kpi(body, "piped")
    year = served["asOf"][:4]
    ((total, rdp, pipedhh),) = fixture_db(
        f"""SELECT sum("Total Households"), sum("At or Above RDP Households"), sum("Piped Infrastructure Households")
            FROM "18_1_water_access_and_coverage_master"
            WHERE "Region" LIKE 'City of Cape Town%' AND "Category" = 'Total' AND "Time Frame" = 'April {year}'"""
    )
    assert served["value"] == round(float(rdp)) and piped["value"] == round(float(pipedhh) / float(total) * 100, 1)
    for kid in ("sanitation", "plants", "reservoirs", "pumps", "pipelines"):
        assert kpi(body, kid)["value"] is None and kpi(body, kid)["unavailableReason"]
    nrw = kpi(body, "nrw")
    assert nrw["value"] is not None and 0 <= nrw["value"] <= 100
    nat = (await client.get("/api/v1/services/summary")).json()
    assert kpi(nat, "served")["value"] > kpi(body, "served")["value"]  # the country sums every municipality


async def test_services_panels(client: httpx.AsyncClient) -> None:
    B = "/api/v1/services"
    nrw = (await client.get(f"{B}/timeseries", params={"metric": "nrw", "period": "historical"})).json()
    c.SeriesSet.model_validate(nrw)
    assert len(nrw["series"][0]["data"]) >= 15 and nrw["refLines"][0]["y"] == 20
    it = (await client.get(f"{B}/timeseries", params={"metric": "interruptions"})).json()
    assert [s["key"] for s in it["series"]] == ["planned", "unplanned"]
    for metric in ("coverage", "service-levels", "urban-rural"):
        bd = await client.get(f"{B}/breakdown", params={"metric": metric})
        assert bd.status_code == 200, (metric, bd.text)
    sl = (await client.get(f"{B}/breakdown", params={"metric": "service-levels"})).json()
    shares = [sum(s["values"][i] or 0 for s in sl["series"]) for i in range(9)]
    assert all(abs(x - 100) < 0.5 or x == 0 for x in shares)
    ur = (await client.get(f"{B}/breakdown", params={"metric": "urban-rural"})).json()
    assert ur["categories"][0] == "RDP access (%)" and [s["key"] for s in ur["series"]] == ["urban", "rural"]
    tbl = (await client.get(f"{B}/table", params={"metric": "nrw"})).json()
    assert tbl["rows"] and {r["status"] for r in tbl["rows"]} <= {"Above target", "Within target"}
    for layer in ("coverage", "rdp", "nrw", "reliability"):
        m = await client.get(f"{B}/map", params={"layer": layer})
        assert m.status_code == 200 and m.json()["values"], layer
    for kind, key in (("forecast", "nrw"), ("breakdown", "asset-condition"), ("table", "plants"), ("map", "wtp")):
        r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
        assert r.status_code == 501, (kind, key)


async def test_municipality_entities(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    ents = (await client.get("/api/v1/services/entities")).json()
    assert ents["total"] == 213 and ents["items"][0]["type"] == "municipality"
    wc = (await client.get("/api/v1/services/entities", params={"province": "WC"})).json()
    assert 0 < wc["total"] < 213 and all(e["province"] == "WC" for e in wc["items"])
    card = (await client.get("/api/v1/services/entities/LM018")).json()  # City of Cape Town
    c.EntityDetail.model_validate(card)
    f = {x["label"].split(" (")[0]: x for x in card["fields"]}
    card_year = f["Total households"]["label"].rsplit("(", 1)[1].rstrip(")")  # the latest complete year (D5)
    ((total,),) = fixture_db(
        f"""SELECT "Total Households" FROM "18_1_water_access_and_coverage_master"
            WHERE "Region" LIKE 'City of Cape Town%' AND "Category" = 'Total' AND "Time Frame" = 'April {card_year}'"""
    )
    assert f["Total households"]["value"] == round(float(total))
    assert int(card_year) < 2026  # 2026 is incomplete: never a card year
    assert (await client.get("/api/v1/services/entities/DC44")).status_code == 404
    t = (await client.get("/api/v1/tariffs/entities/LM018")).json()
    labels = [x["label"].split(" (")[0] for x in t["fields"]]
    assert "Municipal block tariff" in labels and "Cost recovery" in labels


async def test_tariffs(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    B = "/api/v1/tariffs"
    body = (await client.get(f"{B}/summary", params={"municipality": "CPT"})).json()
    c.KpiSet.model_validate(body)
    muni = kpi(body, "municipal")
    year = muni["asOf"][:4]
    ((tariff, cost),) = fixture_db(
        f"""SELECT avg("Block tariff rates (R/kL per consumption block)"),
                   avg("Average treatment cost per kL produced (R/kL)")
            FROM "19_2_municipal_water_tariff_master"
            WHERE "Region" LIKE 'City of Cape Town%' AND "Standardized_Year" = {year}"""
    )
    assert muni["value"] == round(float(tariff), 2)
    assert kpi(body, "recovery")["value"] == round(float(tariff) / float(cost) * 100)
    for kid in ("raw-revenue", "bulk", "affordability"):
        assert kpi(body, kid)["value"] is None
    for metric in ("comparison", "cost-vs-tariff", "increases", "muni-nrw"):
        bd = await client.get(f"{B}/breakdown", params={"metric": metric})
        assert bd.status_code == 200 and len(bd.json()["categories"]) == 9, (metric, bd.text)
    rank = (await client.get(f"{B}/table", params={"metric": "ranking"})).json()
    assert rank["rows"] and rank["rows"][0]["tariff"] >= rank["rows"][-1]["tariff"]
    mx = (await client.get(f"{B}/matrix", params={"metric": "province-increase"})).json()
    c.MatrixSet.model_validate(mx)
    assert len(mx["rows"]) == 9 and len(mx["cols"]) == 4
    for layer in ("tariff", "increase", "recovery", "nrw"):
        m = await client.get(f"{B}/map", params={"layer": layer, "province": "WC"})
        assert m.status_code == 200 and "CPT" in m.json()["values"], layer
    for kind, key in (("forecast", "tariff"), ("breakdown", "blocks"), ("map", "affordability")):
        r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
        assert r.status_code == 501, (kind, key)
    h = (await client.get("/api/v1/health")).json()
    assert {m["module"]: m["status"] for m in h["modules"]}["services"] == "live"
    assert {m["module"]: m["status"] for m in h["modules"]}["tariffs"] == "live"
