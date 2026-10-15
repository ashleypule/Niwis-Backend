"""The database view's catalogue: unit parsing, label rules, completeness and the axis rule (A1, A3)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.core.units import LABEL_OVERRIDES, SPECTRAL_INDICES, SUFFIXES, column_label, name_of, split_unit, with_unit
from app.registry.catalogue import (
    NEW_COLUMN,
    SchemaColumn,
    build_catalogue,
    category_label,
    load_catalogue,
    schema_of,
)
from app.registry.registry import load_registry
from tests.conftest import TEST_DB_URL, requires_db

ROOT = Path(__file__).resolve().parent.parent
CAT = load_catalogue()
REG = load_registry()


# ---------------------------------------------------------------- unit parsing


@pytest.mark.parametrize(("suffix", "unit"), SUFFIXES)
def test_every_suffix_parses(suffix: str, unit: str | None) -> None:
    for case in (suffix, suffix.upper(), suffix.title()):  # `_C` / `_c`, `_Days` / `_days`, `_Percent`
        assert split_unit(f"Some_Name{case}") == ("Some_Name", unit), case


@pytest.mark.parametrize(
    ("column", "unit"),
    [
        ("daily_rainfall_mm_day", "mm/day"),
        ("monthly_rainfall_mm_month", "mm/month"),
        ("rainfall_intensity_mm_h", "mm/h"),
        ("TAMSAT_Satellite_Rainfall_Estimate_mm_dekad", "mm/dekad"),
        ("Tmin_C", "°C"),
        ("tmin_c", "°C"),
        ("relative_humidity_pct", "%"),
        ("Streamflow_Anomaly_percent", "%"),
        ("river_discharge_m3_s", "m³/s"),
        ("Peak_Flow_Rates_m3s", "m³/s"),
        ("Daily_Surface_Runoff_Volume_m3_day", "m³/day"),
        ("dam_storage_million_m3", "Mm³"),
        ("Monthly_Runoff_Totals_Million_m3_month", "Mm³/month"),
        ("MAR_m3_year", "m³/year"),
        ("Catchment_Area_km2", "km²"),
        ("solar_radiation_wm2", "W/m²"),
        ("wind_speed_2m_ms", "m/s"),
        ("Daily_VPD_kPa", "kPa"),
        ("Soil_Moisture_Prior_m3m3", "m³/m³"),
        ("borehole_water_level_m_bgl", "m bgl"),
        ("CDD_Days", "days"),
        ("Heatwave_Duration_Max_Consecutive_Days", "days"),
        ("Runoff_Coefficient_dimensionless", None),
        ("SVI_standardised", None),
        ("ndvi", None),
        ("spi_3", None),
        ("RAI_Rainfall_Anomaly_Index", None),
        ("R10mm_Days", "days"),  # "mm" inside the name is not the unit
    ],
)
def test_unit_from_column_name(column: str, unit: str | None) -> None:
    assert split_unit(column)[1] == unit


@pytest.mark.parametrize(
    ("column", "label"),
    [
        ("daily_rainfall_mm_day", "Daily Rainfall (mm/day)"),
        ("SPI_3_Short_Term_Agricultural_Drought", "SPI 3 – Short Term Agricultural Drought"),
        ("Heatwave_Duration_Max_Consecutive_Days", "Heatwave Duration Max Consecutive (days)"),
        ("et0_mm_day", "ET₀ (mm/day)"),
        ("Potential_Evapotranspiration_ET0_mm", "Potential Evapotranspiration ET₀ (mm)"),
        ("spi_3", "SPI 3"),
        ("ndvi", "NDVI"),
        ("RAI_Rainfall_Anomaly_Index", "RAI – Rainfall Anomaly Index"),
        ("SPI_1_month", "SPI 1 Month"),
        ("PRCPTOT_mm", "PRCPTOT (mm)"),
        ("SDII_mm_per_wet_day", "SDII (mm/wet day)"),
        ("Rx1day_mm", "Rx1day (mm)"),
        ("Q90_LowFlow_m3_year", "Q90 LowFlow (m³/year)"),
        ("Curve_Number_CN", "Curve Number CN"),
        ("Actual_ET_from_Catchment_mm", "Actual ET from Catchment (mm)"),
        ("Number_of_Rainy_Days_days_month", "Number of Rainy Days (days/month)"),
        ("MAR_m3_year", "MAR (m³/year)"),
        ("Growing_Season_Temp_C", "Growing Season Temp (°C)"),
        ("LST_Day_C", "LST Day (°C)"),
        ("grace_tws", "GRACE TWS"),
    ],
)
def test_label_rules(column: str, label: str) -> None:
    base, unit = split_unit(column)
    assert with_unit(name_of(base), unit) == label


def test_overrides_only_fix_units_and_say_why() -> None:
    for (table, column), ov in LABEL_OVERRIDES.items():
        assert ov.why, (table, column)
        name, unit, why = column_label(table, column)
        assert unit == ov.unit and why == ov.why
        assert name == name_of(split_unit(column)[0])  # the label words are never changed


@pytest.mark.parametrize(
    ("table", "label"),
    [
        ("6.1_core_rainfall_variables_master", "Core Rainfall Variables"),
        ("meteorological_drought_master", "Meteorological Drought"),
        ("4_6_streamflow_and_climate_linkage_annual _master", "Streamflow and Climate Linkage Annual"),
        ("5.1_primary_satellite_vegetation_variables_sansa_master", "Primary Satellite Vegetation Variables SANSA"),
        ("satellite_remote_sensing", "Satellite Remote Sensing"),
    ],
)
def test_category_label(table: str, label: str) -> None:
    assert category_label(table) == label


def test_spec_tags() -> None:
    spec = {c.table: c.spec for c in CAT.categories}
    assert spec["6.1_core_rainfall_variables_master"] == "6.1"
    assert spec["meteorological_drought_master"] == "3.1"
    assert spec["4_6_streamflow_and_climate_linkage_annual _master"] == "4.6"
    assert all(spec.values())


# ---------------------------------------------------------------- completeness


def test_every_registered_table_is_a_category() -> None:
    assert [c.table for c in CAT.categories] == [d.table for d in REG.datasets.values()]
    assert len({c.id for c in CAT.categories}) == len(CAT.categories)


def test_every_column_appears_exactly_once() -> None:
    """Every column of every registered table (Phase 0 inventory) is a key or a live / withheld / duplicate variable."""
    inventory = json.loads((ROOT / "docs/db_inventory.json").read_text(encoding="utf-8"))["tables"]
    for c in CAT.categories:
        listed = [*c.keys, *(x.column for x in c.columns)]
        assert len(listed) == len(set(listed)), c.table
        assert set(listed) == {col["name"] for col in inventory[c.table]["columns"]}, c.table
        assert {x.status for x in c.columns} <= {"live", "withheld", "duplicate"}


def test_status_follows_the_registry() -> None:
    for c in CAT.categories:
        ds = REG.datasets[c.table]
        for x in c.columns:
            col = ds.columns.get(x.column)
            if col is None:
                assert (x.status, x.reason) == ("withheld", NEW_COLUMN)
            elif col.role == "withheld":
                assert x.status == "withheld" and x.reason == col.reason
            elif col.role == "duplicate":
                assert x.status == "duplicate" and x.group is None and x.duplicate_of
                t, _, col_name = x.duplicate_of.rpartition(".")
                twin = next(k for k in CAT.categories if k.table == t).column(col_name)
                assert twin is not None and twin.status == "live", x.duplicate_of
            elif col.raw_reason:
                assert x.status == "withheld" and x.reason == col.raw_reason
            else:
                assert x.status == "live" and x.reason is None


def test_withheld_by_ground_rules() -> None:
    """DATA_GAPS §3–5 and §15: these are never live."""
    status = {(c.table, x.column): x.status for c in CAT.categories for x in c.columns}
    must = [
        ("meteorological_drought_master", "wind_speed_2m_ms"),
        ("meteorological_drought_master", "solar_radiation_wm2"),
        ("hydrological_drought_master", "river_discharge_m3_s"),
        ("hydrological_drought_master", "surface_runoff_m3_s"),
        ("hydrological_drought_master", "baseflow_separation_m3_s"),
        ("hydrological_drought_master", "fdc_percentiles_m3_s"),
        ("hydrological_drought_master", "ssi_dimensionless"),
        ("hydrological_drought_master", "borehole_water_level_m_bgl"),
        ("hydrological_drought_master", "dam_storage_million_m3"),
        ("satellite_remote_sensing", "grace_tws"),
    ]
    for key in must:
        assert status[key] == "withheld", key
    for c in CAT.categories:
        if c.table.startswith(("4_4", "4_5", "4_6", "5.1")):
            assert {x.status for x in c.columns} == {"withheld"}, c.table


def test_catalogue_is_in_sync_with_registry_and_builder() -> None:
    """Rebuilding from the schema recorded in the YAML reproduces it: re-run scripts/build_catalogue.py after changing
    the registry or app/core/units.py."""
    rebuilt = build_catalogue(REG, schema_of(CAT), CAT.generated, CAT.source)
    assert rebuilt == CAT


def test_new_columns_and_tables_are_picked_up_without_code_changes() -> None:
    schema = schema_of(CAT)
    table = "6.1_core_rainfall_variables_master"
    schema[table] = [*schema[table], SchemaColumn(column="Daily_Rainfall_Max_mm_h", db_type="double precision")]
    schema["13_new_quality_master"] = [SchemaColumn(column="ph", db_type="double precision")]
    cat = build_catalogue(REG, schema, CAT.generated)
    new = next(c for c in cat.categories if c.table == table).column("Daily_Rainfall_Max_mm_h")
    assert new is not None
    assert (new.label, new.status, new.reason) == ("Daily Rainfall Max (mm/h)", "withheld", NEW_COLUMN)
    assert new.group is not None
    assert "13_new_quality_master" in [u.table for u in cat.unregistered]


def test_registry_column_missing_from_database_fails() -> None:
    schema = schema_of(CAT)
    schema["4_2_wet_dry_spells_master"] = [x for x in schema["4_2_wet_dry_spells_master"] if x.column != "CDD_Days"]
    with pytest.raises(ValueError, match="CDD_Days"):
        build_catalogue(REG, schema, CAT.generated)


# ---------------------------------------------------------------- groups and the axis rule (A3)


def test_axis_rule_one_unit_per_group() -> None:
    for c in CAT.categories:
        cols = {x.column: x for x in c.columns}
        for g in c.groups:
            members = [cols[k] for k in g.columns]
            assert len({m.unit for m in members}) == 1, (c.table, g.id)  # same unit → one axis
            assert len({m.resolution for m in members}) == 1, (c.table, g.id)
            assert g.unit == (members[0].unit or g.unit)
            if g.unit == "index":  # unitless indices of one family only
                assert len({m.name.split(" ")[0] in SPECTRAL_INDICES for m in members}) == 1
            assert 1 <= len(g.default_visible) <= 3 and set(g.default_visible) <= set(g.columns)


def test_ndvi_and_vci_never_share_an_axis() -> None:
    sat = next(c for c in CAT.categories if c.table == "satellite_remote_sensing")
    by_col = {x.column: x.group for x in sat.columns}
    assert by_col["ndvi"] != by_col["vci"] and by_col["vci"] == by_col["vhi"]
    ndvi, vci = sat.group(str(by_col["ndvi"])), sat.group(str(by_col["vci"]))
    assert ndvi is not None and vci is not None and (ndvi.unit, vci.unit) == ("index", "%")


def test_every_charted_column_is_in_one_group() -> None:
    for c in CAT.categories:
        in_groups = Counter(k for g in c.groups for k in g.columns)
        assert all(n == 1 for n in in_groups.values()), c.table
        charted = {x.column for x in c.columns if x.status != "duplicate"}
        assert set(in_groups) == charted, c.table
        assert all((x.group is not None) == (x.status != "duplicate") for x in c.columns)
        assert {x.column for x in c.columns if x.default_visible} == {k for g in c.groups for k in g.default_visible}


def test_default_visible_prefers_live_columns() -> None:
    for c in CAT.categories:
        status = {x.column: x.status for x in c.columns}
        for g in c.groups:
            if any(status[k] == "live" for k in g.columns):
                assert all(status[k] == "live" for k in g.default_visible), (c.table, g.id)


def test_meteorological_groups() -> None:
    met = next(c for c in CAT.categories if c.table == "meteorological_drought_master")
    labels = {g.label for g in met.groups}
    assert {"Evapotranspiration (mm/day)", "Relative Humidity (%)", "SPEI (index)"} <= labels
    assert "Solar Radiation (W/m²)" in labels and "Wind Speed 2m (m/s)" in labels  # withheld, but listed with a reason


@requires_db
def test_fixture_database_has_the_catalogued_columns() -> None:
    """The recorded test database holds exactly the catalogued columns of every registered table."""
    import psycopg

    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
        ).fetchall()
    have: dict[str, set[str]] = {}
    for t, col in rows:
        have.setdefault(str(t), set()).add(str(col))
    for c in CAT.categories:
        assert have.get(c.table) == {*c.keys, *(x.column for x in c.columns)}, c.table
