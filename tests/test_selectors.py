"""Selectors (`set`, `var`, `kind`, `scale`, `window`, `level`, `index`, `order`, `scenario`) are honoured.

BACKEND_ISSUES #1: `vegetation/timeseries?metric=indices&set=…` always returned NDVI/EVI/SAVI/MSAVI2, and every other
panel selector the frontend sends was silently ignored too. For every selector a service declares
(app.modules.catalog.selectors), each value must return a different response from every other value, and an unknown
value must be a 400 listing the valid ones.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.modules import catalog
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
SCOPE = {"municipality": "DC6"}  # Namakwa: present in every fixture table

CASES = [
    (module, kind, key, sel)
    for module in catalog.SERVICE_MODULES
    for (kind, key), sels in catalog.selectors(module).items()
    for sel in sels
]


def fingerprint(r: httpx.Response) -> str:
    """What the panel would show: status, and the series / categories / rows / values (not titles or notes)."""
    if r.status_code != 200:
        return f"{r.status_code}:{r.json().get('missing')}"
    b = r.json()
    parts: dict[str, Any] = {k: b.get(k) for k in ("series", "categories", "rows", "values", "axes", "unit") if k in b}
    return json.dumps(parts, sort_keys=True)


@pytest.mark.parametrize(("module", "kind", "key", "sel"), CASES, ids=lambda x: getattr(x, "name", str(x)))
async def test_each_selector_value_returns_a_different_response(
    client: httpx.AsyncClient, module: str, kind: str, key: str, sel: catalog.Selector
) -> None:
    param = catalog.KINDS[kind]
    scope = {} if kind == "table" else SCOPE  # rankings need more than one district to differ in order
    seen: dict[str, str] = {}
    for value in sel.values:
        r = await catalog.fetch(client, f"/api/v1/{module}/{kind}", {param: key, sel.name: value, **scope})
        assert r.status_code in (200, 501), (value, r.text)
        fp = fingerprint(r)
        clash = [v for v, f in seen.items() if f == fp]
        assert not clash, f"{module}/{kind}?{param}={key}: {sel.name}={value} returns the same as {clash}"
        seen[value] = fp
    # the default is one of the values and is what an absent selector returns
    default = sel.default.split(",")[0] if sel.multi else sel.default
    r = await catalog.fetch(client, f"/api/v1/{module}/{kind}", {param: key, **scope})
    if not sel.multi:
        assert fingerprint(r) == seen[default]


@pytest.mark.parametrize(("module", "kind", "key", "sel"), CASES, ids=lambda x: getattr(x, "name", str(x)))
async def test_unknown_selector_value_is_a_400(
    client: httpx.AsyncClient, module: str, kind: str, key: str, sel: catalog.Selector
) -> None:
    r = await client.get(f"/api/v1/{module}/{kind}", params={catalog.KINDS[kind]: key, sel.name: "bogus", **SCOPE})
    assert r.status_code == 400, r.text
    issue = r.json()["issues"][0]
    assert issue["param"] == sel.name and issue["allowed"] == list(sel.values)


# ---------------------------------------------------------------- vegetation indices (the reported case)

IDX = "/api/v1/vegetation/timeseries"


async def test_indices_set_returns_the_requested_indices(client: httpx.AsyncClient) -> None:
    body = (await client.get(IDX, params={"metric": "indices", "set": "vci,vhi,tci", **SCOPE})).json()
    assert [s["key"] for s in body["series"]] == ["vci", "vhi", "tci"]
    assert [a["unit"] for a in body["axes"]] == ["%"]
    assert all(any(p[1] is not None for p in s["data"]) for s in body["series"])
    assert "unavailable" not in body
    assert body["source"] == ["satellite_remote_sensing"]


async def test_withheld_index_is_a_null_series_with_a_reason(client: httpx.AsyncClient) -> None:
    body = (await client.get(IDX, params={"metric": "indices", "set": "ndwi,ndii", **SCOPE})).json()
    s = {x["key"]: x for x in body["series"]}
    assert list(s) == ["ndwi", "ndii"]
    assert any(p[1] is not None for p in s["ndwi"]["data"])
    assert all(p[1] is None for p in s["ndii"]["data"])
    (u,) = body["unavailable"]
    assert u["field"] == "series.ndii" and "5.1" in u["reason"]


async def test_indices_default_is_unchanged(client: httpx.AsyncClient) -> None:
    body = (await client.get(IDX, params={"metric": "indices", **SCOPE})).json()
    assert [s["key"] for s in body["series"]] == ["ndvi", "evi", "savi", "msavi2"]
    assert {u["field"] for u in body["unavailable"]} == {"series.evi", "series.savi", "series.msavi2"}


async def test_unknown_index_lists_the_valid_ones(client: httpx.AsyncClient) -> None:
    r = await client.get(IDX, params={"metric": "indices", "set": "vci,foo"})
    assert r.status_code == 400
    issue = r.json()["issues"][0]
    assert issue["invalid"] == ["foo"] and "vci" in issue["allowed"] and "ndii" in issue["allowed"]


async def test_set_with_more_units_than_axes_is_a_400(client: httpx.AsyncClient) -> None:
    r = await client.get(IDX, params={"metric": "indices", "set": "ndvi,vci,lst"})
    assert r.status_code == 400 and r.json()["issues"][0]["units"] == ["—", "%", "°C"]


async def test_withheld_index_trend_is_501(client: httpx.AsyncClient) -> None:
    r = await client.get(IDX, params={"metric": "index-trend", "index": "evi"})
    assert r.status_code == 501 and r.json()["missing"] == ["evi"]


async def test_indicator_trends_scenario_is_not_answered_with_observations(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/climate/timeseries", params={"metric": "indicator-trends", "scenario": "rcp85"})
    assert r.status_code == 501 and r.json()["missing"] == ["projections:rcp85"]


async def test_rainfall_anomaly_window_sums_months(client: httpx.AsyncClient, fixture_db: Any) -> None:
    """window=3 → (P₃ − N₃) / N₃ for the three complete months ending each month."""
    q = {"metric": "anomaly", "municipality": "DC6", "from": "2025-01-01", "to": "2025-06-30"}
    one = (await client.get("/api/v1/rainfall/timeseries", params=q)).json()
    three = (await client.get("/api/v1/rainfall/timeseries", params={**q, "window": "3"})).json()
    assert one["title"] == "Monthly rainfall anomaly" and three["title"] == "3-month rainfall anomaly"
    ((v,),) = fixture_db(
        """WITH m AS (SELECT substr("Date", 1, 7) ym, sum("Daily_Rainfall_Total_mm") p
                      FROM "6.1_core_rainfall_variables_master"
                      WHERE "District_Name" = 'Namakwa' AND "Date" BETWEEN '2025-04-01' AND '2025-06-30' GROUP BY 1),
                n AS (SELECT substr("Date", 1, 7) ym, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") nrm
                      FROM "6.1_core_rainfall_variables_master"
                      WHERE "District_Name" = 'Namakwa' AND "Date" BETWEEN '2025-04-01' AND '2025-06-30' GROUP BY 1)
           SELECT round(((SELECT sum(p) FROM m) - (SELECT sum(nrm) FROM n))::numeric
                        / (SELECT sum(nrm) FROM n)::numeric * 100, 1)"""
    )
    june = {p[0]: p[1] for s in three["series"] for p in s["data"] if p[1] is not None}["2025-06-01"]
    assert june == pytest.approx(float(v), abs=0.15)
