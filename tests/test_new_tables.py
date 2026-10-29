"""The §8–§16 tables (registered 2026-10-20): long-format reads, rows after today, impossible values, yes / no flags,
identical duplicate rows, unit repair and the per-module completeness of months (docs/DECISIONS.md D1–D18)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core import units
from app.registry.catalogue import get_catalogue
from app.registry.registry import load_registry
from tests.conftest import requires_db

REG = load_registry()
CAT = get_catalogue()
BH = "10_1_borehole_and_aquifer_master"


# Tables that appeared in the database on 2026-10-23 (during the forecasting work): listed as unregistered, as the
# catalogue does for every new table, until they are profiled and plausibility-checked (DATA_GAPS §22).
PENDING_REVIEW = {"17_1_water_quantity_assessment_transfers_master", "17_2_national_water_balance_master"}


def test_every_new_table_is_registered_or_ignored_with_a_reason() -> None:
    assert {u.table for u in CAT.unregistered} <= PENDING_REVIEW
    assert "16_2_swsa_strategic_water_matrix_master" in REG.ignored_tables
    new = [ds for ds in REG.datasets.values() if ds.db_module >= 6]
    assert len(new) == 21
    for ds in new:
        assert ds.location.column == "district_name", ds.table


def test_long_format_columns_read_one_variable() -> None:
    ds = REG.datasets[BH]
    assert ds.long is not None
    sql = ds.column_sql("groundwater_table_depth_m_bgl")
    assert sql == '(CASE WHEN "variable_name" = \'groundwater_table_depth_m_bgl\' THEN "value" END)'
    hg = REG.datasets["11_1_hydrogeological"]  # long part and wide columns side by side
    assert "CASE WHEN" in hg.column_sql("Depth to water table")
    assert hg.column_sql("Transmissivity") == '"Transmissivity"'


def test_units_of_the_long_table_follow_the_names_and_the_stored_units() -> None:
    """10_1's units come from its variable names; they agree with the stored unit text (repaired) except the two
    documented overrides (D6, D7)."""
    stored = {  # 10_1 `unit` column as stored (valid UTF-8, checked 2026-10-20)
        "annual_abstraction_volume_m3_yr": "m³/year",
        "annual_recharge_rate_mm_yr": "mm/year",
        "borehole_pumping_rate_m3_h": "m³/h",
        "groundwater_table_depth_m_bgl": "m bgl",
        "hydraulic_conductivity_m_day": "m/day",
        "hydraulic_head_amsl_m": "m amsl",
        "hydraulic_head_m_amsl": "m bgl",
        "recharge_fraction_percent": "%",
        "saturated_thickness_m": "m",
        "seasonal_water_table_fluctuation_m": "m/season",
        "sgi_dimensionless": "dimensionless",
        "storativity_specific_yield_dimensionless": "dimensionless",
        "transmissivity_m2_day": "m²/day",
    }
    cat = next(c for c in CAT.categories if c.table == BH)
    for var, text in stored.items():
        x = cat.column(var)
        assert x is not None, var
        if var == "hydraulic_head_m_amsl":  # stored "m bgl", values are elevations (D7)
            assert x.unit == "m amsl" and x.unit_override
            continue
        assert x.unit == units.repair_unit(text), var


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("m�/year", "m³/year"),
        ("mÂ³/h", "m³/h"),
        ("m2/day", "m²/day"),
        ("m³/year", "m³/year"),
        ("dimensionless", None),
        (" m bgl ", "m bgl"),
        (None, None),
    ],
)
def test_repair_unit(text: str | None, unit: str | None) -> None:
    assert units.repair_unit(text) == unit


def test_every_new_column_has_a_status_and_withheld_ones_a_reason() -> None:
    for c in CAT.categories:
        if REG.datasets[c.table].db_module < 6:
            continue
        for x in c.columns:
            assert x.status in ("live", "withheld"), (c.table, x.column)
            if x.status == "withheld":
                assert x.reason and "not reviewed" not in x.reason, (c.table, x.column)


def test_reservoir_storage_from_8_1_is_withheld() -> None:
    """D2: 51 of 52 districts hold one storage value on every day — storage is not served from 8_1 or anywhere."""
    cat = next(c for c in CAT.categories if c.table == "8_1_reservoir_operational_master")
    for col in ("storage_volume_m3", "pct_fsc", "inflow_m3_s", "outflow_m3_s", "water_level_m_amsl"):
        x = cat.column(col)
        assert x is not None and x.status == "withheld", col
    assert REG.canonical_source("dam_storage") is None and REG.canonical_source("dam_level_pct") is None


async def test_value_ranges_drop_impossible_values(fixture_db: Callable[[str], list[Any]]) -> None:
    """D1: a negative SASS5 score or ammonia concentration is never served; valid values are unchanged."""
    ds = REG.datasets["15.1_ecological_variables_master"]
    expr = ds.column_sql("sass5_score")
    rows = fixture_db(f'SELECT {expr} FROM (VALUES (-130.4::float8), (98.5::float8)) AS t("sass5_score")')
    assert [r[0] for r in rows] == [None, 98.5]
    ds = REG.datasets["15.1_ecological_variables_master"]
    expr = ds.column_sql("fibi")
    rows = fixture_db(f'SELECT {expr} FROM (VALUES (108.8::float8), (55.0::float8), (-1.0::float8)) AS t("fibi")')
    assert [r[0] for r in rows] == [None, 55.0, None]


async def test_compliance_flags_are_served_as_percent(fixture_db: Callable[[str], list[Any]]) -> None:
    ds = REG.datasets["15.3_hydrological_RWQO_variables_master"]
    expr = ds.column_sql("Low_Flow_Compliant")
    rows = fixture_db(f'SELECT {expr} FROM (VALUES (true), (false), (NULL::boolean)) AS t("Low_Flow_Compliant")')
    assert [r[0] for r in rows] == [100.0, 0.0, None]
    cat = next(c for c in CAT.categories if c.table == ds.table)
    x = cat.column("Low_Flow_Compliant")
    assert x is not None and x.unit == "%" and x.unit_override


@pytest.mark.db
@requires_db
async def test_rows_after_today_are_never_served(client: httpx.AsyncClient) -> None:
    """D4: 8_1 and 10_1 hold rows to 2026-12-31; no category series point lies after today."""
    today = dt.date.today()
    for module, table in (("dams", "8_1_reservoir_operational_master"), ("groundwater", BH)):
        cat = next(c for c in CAT.categories if c.table == table)
        for g in cat.groups_for(module):
            r = await client.get(
                f"/api/v1/{module}/category/{cat.id}/{g.id}", params={"vars": "all", "period": "historical"}
            )
            assert r.status_code == 200, (table, g.id, r.text[:200])
            for s in r.json()["series"]:
                for d, v in s["data"]:
                    if v is not None:
                        assert dt.date.fromisoformat(d) <= today, (table, g.id, s["key"], d)


@pytest.mark.db
@requires_db
async def test_long_table_values_equal_sql(client: httpx.AsyncClient, fixture_db: Callable[[str], list[Any]]) -> None:
    """A monthly 10_1 variable (stamped on the 15th) for one district equals the stored value."""
    cat = next(c for c in CAT.categories if c.table == BH)
    g = cat.group_of("groundwater", "hydraulic_head_amsl_m")
    assert g is not None
    r = await client.get(
        f"/api/v1/groundwater/category/{cat.id}/{g.id}",
        params={"vars": "hydraulic_head_amsl_m", "municipality": "DC6", "period": "12M"},
    )
    assert r.status_code == 200, r.text[:300]
    s = next(x for x in r.json()["series"] if x["key"] == "hydraulic_head_amsl_m")
    pts = {d: v for d, v in s["data"] if v is not None}
    assert pts
    d0 = max(pts)
    ((v,),) = fixture_db(
        f"""SELECT value FROM "{BH}" WHERE district_name = 'Namakwa' AND variable_name = 'hydraulic_head_amsl_m'
            AND date_trunc('month', date)::date = DATE '{d0}'"""
    )
    assert pts[d0] == round(float(v), 2)


@pytest.mark.db
@requires_db
async def test_identical_duplicate_rows_are_read_once(client: httpx.AsyncClient) -> None:
    """D18: 11.2 stores every district-month three times; the loader returns one value per district-month."""
    state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    df = await state.data.monthly("rd_direct_recharge")
    assert not df.empty
    assert not df.duplicated(["district", "date"]).any()


@pytest.mark.db
@requires_db
async def test_name_filters_use_the_tables_own_spellings(client: httpx.AsyncClient) -> None:
    """SQL name filters are exact: each table's own spellings are discovered (satellite_remote_sensing writes
    'cape town', 9_1 writes 'UMgungundlovu' / 'Pixley Ka Seme'), so no district drops out of a scope."""
    state = client._transport.app.state.niwis  # type: ignore[attr-defined]
    ds = state.registry.datasets["satellite_remote_sensing"]
    assert "cape town" in await state.data.names(ds, "CPT")
    spell = await state.data.spellings(ds)
    assert set(spell) == {"DC44", "CPT", "DC6"}
