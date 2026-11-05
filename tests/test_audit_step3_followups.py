"""Audit step 3, part B: the backend follow-ups of step 2 (frontend BACKEND_ISSUES §15, DECISIONS D49)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from app.core.provenance import period_end
from app.core.response_checks import register_units, unit_violations
from app.core.units import canon_unit
from app.modules import catalog
from app.schemas import contract as c
from tests.conftest import requires_db

RECORDED = Path(__file__).parents[2] / "Niwis Frontend" / "tests" / "fixtures" / "backend"
# query parameters that choose what a panel shows; the scope, period and key parameters are checked elsewhere
SELECTOR_PARAMS = {"group", "index", "kind", "level", "order", "param", "scale", "scenario", "set", "var", "window"}
needs_recording = pytest.mark.skipif(not (RECORDED / "index.json").exists(), reason="frontend fixtures not present")


# ---------------------------------------------------------------- pure rules


def test_a_snapshot_dated_inside_a_year_ends_in_that_month_not_december() -> None:
    assert period_end(dt.date(2026, 4, 1), "annual") == dt.date(2026, 4, 30)
    assert period_end(dt.date(2025, 1, 1), "annual") == dt.date(2025, 12, 31)


def test_retired_unit_spellings_are_never_served() -> None:
    assert canon_unit("—") in ("", None)
    assert c.EntityField(label="x", value=1, unit="—").unit in ("", None)


@pytest.mark.parametrize(
    ("unit", "label", "want_unit", "want_label"),
    [
        ("mm", "Rainfall (mm)", "mm", "Rainfall"),
        ("", "SPI", "index", "SPI"),
    ],
)
def test_an_axis_label_does_not_repeat_its_unit_and_a_unitless_axis_says_index(
    unit: str, label: str, want_unit: str, want_label: str
) -> None:
    axis = c.Axis(unit=unit, label=label)
    assert (axis.unit, axis.label) == (want_unit, want_label)


def test_entity_fields_say_which_period_they_stand_for() -> None:
    assert c.EntityField(label="x", value=1, asOf="2026-02-01").period_end == "2026-02-28"
    assert c.EntityField(label="x", value=1, asOf="2025-12-31", periodEnd="2025-12-31").period_end == "2025-12-31"


@pytest.mark.parametrize("bad", ["—", "score", "mixed"])
def test_the_response_check_rejects_the_retired_axis_units(bad: str) -> None:
    if register_units() is None:
        pytest.skip("reference/units.json not present")
    assert unit_violations({"axes": [{"unit": bad, "label": "x"}]})


# ---------------------------------------------------------------- the API


@requires_db
@pytest.mark.db
async def test_rwqo_timeseries_honours_param_and_withholds_with_a_reason(client: httpx.AsyncClient) -> None:
    url = "/api/v1/rwqo/timeseries"
    a = (await client.get(url, params={"metric": "params", "param": "do", "municipality": "DC6"})).json()
    b = (await client.get(url, params={"metric": "params", "param": "tds", "municipality": "DC6"})).json()
    assert a["series"][0]["data"] != b["series"][0]["data"]
    withheld = await client.get(url, params={"metric": "params", "param": "ph", "municipality": "DC6"})
    assert withheld.status_code == 501
    assert (await client.get(url, params={"metric": "params", "param": "nope"})).status_code == 400


@requires_db
@pytest.mark.db
async def test_rwqo_ecological_puts_percent_and_scores_on_two_axes(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/rwqo/timeseries", params={"metric": "ecological"})).json()
    assert [a["unit"] for a in body["axes"]] == ["index", "%"]
    assert {s["axis"] for s in body["series"]} == {0, 1}


@requires_db
@pytest.mark.db
async def test_blue_drop_timeseries_honours_group(client: httpx.AsyncClient) -> None:
    url = "/api/v1/blue-drop/timeseries"
    bodies = {
        g: (await client.get(url, params={"metric": "params", "group": g})).json()
        for g in ("key", "micro", "nutrients", "metals")
    }
    names = [tuple(s["name"] for s in b["series"]) for b in bodies.values()]
    assert len(set(names)) == 4


@requires_db
@pytest.mark.db
async def test_exploitation_kpi_is_a_ratio(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/groundwater-reserve/summary", params={"municipality": "DC6"})).json()
    ei = next(k for k in body["kpis"] if k["id"] == "ei")
    assert ei.get("unit") in ("", None)
    assert ei["decimals"] == 2


@requires_db
@pytest.mark.db
async def test_water_quantity_runoff_layer_has_a_volume_scale(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/water-quantity/map", params={"layer": "availability"})).json()
    assert body["scale"] == "volume"


@requires_db
@pytest.mark.db
async def test_tariff_breakdowns_on_a_second_axis_say_its_unit(client: httpx.AsyncClient) -> None:
    for metric in ("cost-vs-tariff", "muni-nrw"):
        body = (await client.get("/api/v1/tariffs/breakdown", params={"metric": metric})).json()
        assert any(s.get("axis") == 1 for s in body["series"]), metric
        assert body["axis2Unit"] == "%", metric


# ---------------------------------------------------------------- what the frontend recorded


def _recorded_bodies() -> list[tuple[str, Any]]:
    out = []
    for p in sorted(RECORDED.glob("*.json")):
        f = json.loads(p.read_text(encoding="utf-8"))
        if p.name != "index.json" and f.get("status") == 200:
            out.append((p.name, f.get("body")))
    return out


@needs_recording
def test_every_unit_in_every_recorded_fixture_is_on_the_register() -> None:
    if register_units() is None:
        pytest.skip("reference/units.json not present")
    problems = [f"{name}: {v}" for name, body in _recorded_bodies() for v in unit_violations(body)]
    assert not problems, f"{len(problems)} violations, first: {problems[:10]}"


@needs_recording
def test_every_selector_the_frontend_sends_is_declared() -> None:
    """A parameter the frontend sends and no service declares is silently ignored (BACKEND_ISSUES #1, §15). A withheld
    panel (501) has nothing to select from, whatever is sent."""
    index = json.loads((RECORDED / "index.json").read_text(encoding="utf-8"))
    undeclared = set()
    for key, name in index.items():
        if json.loads((RECORDED / name).read_text(encoding="utf-8")).get("status") != 200:
            continue
        url = urlsplit(key)
        parts = url.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] not in catalog.SERVICE_MODULES or parts[1] not in catalog.KINDS:
            continue
        module, kind = parts
        query = dict(parse_qsl(url.query))
        metric = query.get(catalog.KINDS[kind], "")
        declared = {s.name for s in catalog.selectors(module).get((kind, metric), ())}
        undeclared |= {f"{module}/{kind}?{metric}&{p}" for p in (query.keys() & SELECTOR_PARAMS) - declared}
    assert not undeclared, sorted(undeclared)
