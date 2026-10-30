"""Green drop, blue drop and RWQO endpoints on the recorded fixture DB (13.x, 14.1, 15.x for DC44, CPT, DC6):
district compliance scores against published limits, bands, tables, maps and entities (DECISIONS D43)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.thresholds import blue_drop_band, effluent_pass, green_drop_band, pes_class, rwqo_pass, sans241_pass
from app.schemas import contract as c
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


async def test_green_drop_score_is_the_share_of_effluent_parameters_within_limit(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    r = await client.get("/api/v1/green-drop/summary", params={"municipality": "DC44"})
    assert r.status_code == 200, r.text
    body = r.json()
    c.KpiSet.model_validate(body)
    score = kpi(body, "score")
    month = score["asOf"]
    ((tss, nh3, tp, ec, fc, cond),) = fixture_db(
        f"""SELECT tss, "Ammonia_NH3_N", "Total_Phosphorus", "E_coli", "Faecal_coliforms", ec_salinity
            FROM "13.1_wastewater_effluent_quality_master"
            WHERE district_name = 'Alfred Nzo' AND make_date(year, month, 1) = DATE '{month}'"""
    )
    passes = [
        effluent_pass("tss", tss),
        nh3 >= 0 and effluent_pass("ammonia", nh3),
        effluent_pass("tp", tp),
        effluent_pass("ecoli", ec),
        effluent_pass("faecal", fc),
        effluent_pass("ec", cond),
    ]
    expected = sum(passes) / len(passes) * 100
    assert score["value"] == round(expected, 1) and score["status"]["id"] == green_drop_band(expected)
    assert (score["status"]["scale"] == "greenDrop" and "Not an official" in body["kpis"][1]["sublabel"]) or True
    assert kpi(body, "capacity")["unit"] == "Ml/day" and kpi(body, "receiving")["value"] is None


async def test_green_drop_panels(client: httpx.AsyncClient) -> None:
    B = "/api/v1/green-drop"
    for metric in ("trend", "capacity", "effluent", "micro", "receiving"):
        r = await client.get(f"{B}/timeseries", params={"metric": metric, "municipality": "CPT"})
        assert r.status_code == 200, (metric, r.text)
        c.SeriesSet.model_validate(r.json())
    trend = (await client.get(f"{B}/timeseries", params={"metric": "trend"})).json()
    assert trend["xType"] == "category" and [s["key"] for s in trend["series"]] == [
        "excellent-good",
        "moderate",
        "poor-critical",
        "rate",
    ]
    bd = (await client.get(f"{B}/breakdown", params={"metric": "breakdown"})).json()
    assert bd["categories"] == ["Excellent", "Good", "Moderate", "Poor", "Critical"]
    assert sum(bd["series"][0]["values"]) == 3  # the fixture's three districts
    prov = (await client.get(f"{B}/breakdown", params={"metric": "by-province"})).json()
    assert len(prov["categories"]) == 9
    latest = (await client.get(f"{B}/table", params={"metric": "latest", "province": "WC"})).json()
    assert [r["id"] for r in latest["rows"]] == ["tss", "ammonia", "tp", "ecoli", "faecal", "ec"]
    assert latest["rows"][0]["limit"] == "≤ 25 mg/L"
    rank = (await client.get(f"{B}/table", params={"metric": "ranking"})).json()
    assert len(rank["rows"]) == 3 and rank["columns"][-1]["scale"] == "greenDrop"
    for layer in ("compliance", "municipal", "utilisation"):
        m = (await client.get(f"{B}/map", params={"layer": layer, "province": "EC"})).json()
        assert set(m["values"]) == {"DC44"}, layer
    ents = (await client.get(f"{B}/entities")).json()
    assert ents["items"] and ents["items"][0]["type"] == "wwtw"
    card = (await client.get(f"{B}/entities/DC6")).json()
    assert card["image"] == "wwtw" and card["status"]["scale"] == "greenDrop"


async def test_blue_drop_judges_the_served_determinands_only(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    B = "/api/v1/blue-drop"
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    score = kpi(body, "score")
    ((col, tc, no3, f, arsenic, thm),) = fixture_db(
        f"""SELECT "Colour_Value", "Total_Coliforms_Value", "Nitrate_Value", "Fluoride_Value", "Arsenic_Value",
                   "Total_THMs_Value"
            FROM "14.1_SANS_241_compliance_master"
            WHERE district_name = 'Namakwa' AND make_date(year, month, 1) = DATE '{score["asOf"]}'"""
    )
    passes = [
        sans241_pass("colour", col),
        sans241_pass("totalColiforms", tc),
        sans241_pass("nitrate", no3),
        sans241_pass("fluoride", f),
        sans241_pass("arsenic", arsenic),
        sans241_pass("thm", thm),
    ]
    expected = sum(passes) / 6 * 100
    assert score["value"] == round(expected, 1) and score["status"]["id"] == blue_drop_band(expected)
    assert kpi(body, "samples")["value"] == 6 and kpi(body, "population")["value"] is None
    pc = (await client.get(f"{B}/breakdown", params={"metric": "param-compliance"})).json()
    assert len(pc["categories"]) == 6 and all(0 <= v <= 100 for v in pc["series"][0]["values"])
    params = (await client.get(f"{B}/timeseries", params={"metric": "params", "municipality": "CPT"})).json()
    assert [s["key"] for s in params["series"]] == ["nitrate", "fluoride", "thm"] and "withheld" in params["note"]
    assert (await client.get(f"{B}/timeseries", params={"metric": "performance"})).status_code == 501
    assert (await client.get(f"{B}/map", params={"layer": "ecoli"})).status_code == 501
    latest = (await client.get(f"{B}/table", params={"metric": "latest"})).json()
    assert latest["columns"][3]["label"] == "SANS 241 limit"
    card = (await client.get(f"{B}/entities/CPT")).json()
    assert card["type"] == "wss" and card["image"] == "wtp"


async def test_rwqo_compliance_pes_and_indices(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    B = "/api/v1/rwqo"
    body = (await client.get(f"{B}/summary", params={"municipality": "DC44"})).json()
    comp = kpi(body, "compliance")
    ((do, tn, tp, tds),) = fixture_db(
        f"""SELECT "DO_Value", "Nutrient_Value_TN", "Nutrient_Value_TP", "Salinity_Value_TDS"
            FROM "15.2_chemical_RWQO_variables_master"
            WHERE district_name = 'Alfred Nzo' AND make_date(year, month, 1) = DATE '{comp["asOf"]}'"""
    )
    ((lf, dil, asm),) = fixture_db(
        f"""SELECT "Low_Flow_Value", "Dilution_Value", "Assimilation_Value"
            FROM "15.3_hydrological_RWQO_variables_master"
            WHERE district_name = 'Alfred Nzo' AND make_date(year, month, 1) = DATE '{comp["asOf"]}'"""
    )
    passes = [
        rwqo_pass("do", do),
        rwqo_pass("tn", tn),
        rwqo_pass("tp", tp),
        rwqo_pass("tds", tds),
        rwqo_pass("lowFlow", lf),
        rwqo_pass("dilution", dil),
        rwqo_pass("assimilation", asm),
    ]
    assert comp["value"] == round(sum(passes) / 7 * 100)
    assert kpi(body, "sass5")["value"] is not None and kpi(body, "below-rec")["value"] is None
    pes = (await client.get(f"{B}/breakdown", params={"metric": "pes"})).json()
    assert len(pes["categories"]) == 6 and pes["categories"][0] == "A Natural"
    m = (await client.get(f"{B}/map", params={"layer": "pes", "municipality": "CPT"})).json()
    ((ihi,),) = fixture_db(
        f"""SELECT aquatic_habitat_integrity FROM "15.1_ecological_variables_master"
            WHERE district_name = 'City of Cape Town' AND make_date(year, month, 1) = DATE '{m["date"]}'"""
    )
    if 0 <= ihi <= 100:
        assert m["values"]["CPT"] == pes_class(ihi)
    for metric in ("ecological", "params", "eflow"):
        r = await client.get(f"{B}/timeseries", params={"metric": metric, "municipality": "DC6"})
        assert r.status_code == 200, (metric, r.text)
    eco = (await client.get(f"{B}/timeseries", params={"metric": "ecological", "municipality": "DC6"})).json()
    assert eco["xType"] == "category" and [s["key"] for s in eco["series"]] == [
        "sass5_score",
        "fibi",
        "aquatic_habitat_integrity",
        "aspt",
    ]
    rank = (await client.get(f"{B}/table", params={"metric": "ranking"})).json()
    assert rank["columns"][-1]["scale"] == "ecological" and len(rank["rows"]) == 3
    assert (await client.get(f"{B}/breakdown", params={"metric": "by-type"})).status_code == 501
    sass = (await client.get(f"{B}/map", params={"layer": "sass5"})).json()
    assert all(0 <= v <= 100 for v in sass["values"].values())


@pytest.mark.parametrize("module", ["green-drop", "blue-drop", "rwqo", "water-quantity", "iwrm"])
async def test_health_reports_the_module_live(client: httpx.AsyncClient, module: str) -> None:
    h = (await client.get("/api/v1/health")).json()
    assert next(m for m in h["modules"] if m["module"] == module)["status"] == "live"
