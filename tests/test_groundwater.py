"""Groundwater Level Monitoring (spec §10): 10_1 telemetry depth, SGI to Aug 2024 only, recharge (11.2) and
abstraction (11.3); values vs SQL on the fixture DB."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.modules.groundwater.service import depth_class
from tests.conftest import requires_db

B = "/api/v1/groundwater"
BH = '"10_1_borehole_and_aquifer_master"'


@pytest.mark.parametrize(("v", "cls"), [(3.0, "< 5"), (5.0, "5–10"), (49.9, "30–50"), (100.0, "> 100")])
def test_depth_classes_follow_the_gwdepth_palette(v: float, cls: str) -> None:
    assert depth_class(v) == cls


def kpi(body: dict[str, Any], kid: str) -> dict[str, Any]:
    return next(k for k in body["kpis"] if k["id"] == kid)


@pytest.mark.db
@requires_db
async def test_level_kpi_is_the_months_mean_telemetry_depth(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    k = kpi(body, "level")
    month = k["asOf"]
    ((v,),) = fixture_db(
        f"""SELECT avg(value) FROM {BH} WHERE district_name = 'Namakwa'
            AND variable_name = 'groundwater_table_depth_m_bgl' AND date_trunc('month', date)::date = DATE '{month}'"""
    )
    assert k["unit"] == "m bgl" and k["value"] == round(float(v), 1)
    assert month <= "2026-10-01"  # rows after today are never read (the fixture runs to 2026-12-20)
    assert kpi(body, "sgi")["value"] is None and "Aug 2024" in kpi(body, "sgi")["unavailableReason"]
    assert kpi(body, "boreholes")["value"] is None


@pytest.mark.db
@requires_db
async def test_recharge_and_abstraction_kpis_vs_sql(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    body = (await client.get(f"{B}/summary", params={"municipality": "DC6"})).json()
    ((rech, abst),) = fixture_db(
        """SELECT (SELECT max("Direct recharge") FROM "11.2_recharge_and_discharge"
                   WHERE district_name = 'Namakwa' AND year = 2025),
                  (SELECT max("Total abstraction") FROM "11.3_aquifer_sustainability"
                   WHERE district_name = 'Namakwa' AND year = 2025)"""
    )
    assert kpi(body, "recharge")["value"] == round(float(rech))
    assert kpi(body, "abstraction")["value"] == round(float(abst) / 1e6, 1)
    assert kpi(body, "recharge")["asOf"] == "2025-01-01"  # 2026 is not a complete year


@pytest.mark.db
@requires_db
async def test_sgi_is_served_to_aug_2024_marked_stale(client: httpx.AsyncClient) -> None:
    s = (await client.get(f"{B}/timeseries", params={"metric": "sgi", "municipality": "DC6"})).json()
    assert s["isStale"] is True and s["periodEnd"] == "2024-08-31"
    assert max(d for d, v in s["series"][0]["data"] if v is not None) == "2024-08-01"
    m = (await client.get(f"{B}/map", params={"layer": "sgi"})).json()
    assert m["isStale"] is True and set(m["values"]) == {"DC44", "CPT", "DC6"}


@pytest.mark.db
@requires_db
async def test_level_change_is_the_difference_of_12_month_means(
    client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]
) -> None:
    m = (await client.get(f"{B}/map", params={"layer": "level-change"})).json()
    last = m["date"]  # the latest sampled month (start)
    ((cur, prev),) = fixture_db(
        f"""WITH mm AS (SELECT date_trunc('month', date)::date m, avg(value) v FROM {BH}
                        WHERE district_name = 'Namakwa' AND variable_name = 'groundwater_table_depth_m_bgl' GROUP BY 1)
            SELECT (SELECT avg(v) FROM mm WHERE m > DATE '{last}' - interval '12 months' AND m <= DATE '{last}'),
                   (SELECT avg(v) FROM mm WHERE m > DATE '{last}' - interval '24 months'
                                            AND m <= DATE '{last}' - interval '12 months')"""
    )
    assert m["values"]["DC6"] == round(float(prev) - float(cur), 2)  # + = higher water table


@pytest.mark.db
@requires_db
@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("timeseries", "quality"),
        ("breakdown", "by-aquifer"),
        ("table", "aquifers"),
        ("map", "status"),
        ("forecast", "level"),
    ],
)
async def test_unavailable_keys(client: httpx.AsyncClient, kind: str, key: str) -> None:
    r = await client.get(f"{B}/{kind}", params={("layer" if kind == "map" else "metric"): key})
    assert r.status_code == 501 and r.json()["detail"]


@pytest.mark.db
@requires_db
async def test_entity_card(client: httpx.AsyncClient) -> None:
    card = (await client.get(f"{B}/entities/DC6")).json()
    labels = [f["label"] for f in card["fields"]]
    assert any(lb.startswith("Depth to water") for lb in labels)
    assert any(lb.startswith("Hydraulic head (telemetry)") for lb in labels)
    head = next(f for f in card["fields"] if f["label"].startswith("Hydraulic head (telemetry)"))
    assert head["unit"] == "m amsl"  # stored as "m bgl"; the values are elevations (DECISIONS D7)
