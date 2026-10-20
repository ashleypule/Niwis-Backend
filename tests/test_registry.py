"""Dataset registry: validation rules and the canonical-source decisions (D4, D5, duplicates)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import get_settings
from app.db import ident
from app.registry.registry import REGISTRY_PATH, Registry, load_registry


@pytest.fixture(scope="module")
def reg() -> Registry:
    return load_registry()


def test_loads_all_tables(reg: Registry) -> None:
    assert len(reg.datasets) == 15
    assert reg.modules == ["climate", "drought", "rainfall", "runoff", "vegetation"]


def test_registered_env_tables_are_in_registry(reg: Registry) -> None:
    env = get_settings().registered_tables
    if not env:
        pytest.skip("no .env table lists")
    listed = {t for tables in env.values() for t in tables}
    assert listed == set(reg.datasets)


def test_each_variable_has_at_most_one_canonical_source(reg: Registry) -> None:
    seen: dict[str, str] = {}
    for ds in reg.datasets.values():
        for name, col in ds.columns.items():
            if col.role == "canonical":
                assert col.var not in seen, f"{col.var}: {seen[col.var]} and {ds.table}.{name}"
                seen[col.var] = f"{ds.table}.{name}"


@pytest.mark.parametrize(
    ("var", "table"),
    [
        ("rain_daily", "6.1_core_rainfall_variables_master"),
        ("rain_monthly", "6.1_core_rainfall_variables_master"),
        ("spi_6", "6.2_rainfall_derived_drought_indices_master"),
        ("spei_6", "meteorological_drought_master"),
        ("tavg", "4_1_temperature_change_master"),
        ("et0_daily", "meteorological_drought_master"),
        ("ndvi", "satellite_remote_sensing"),  # D4: the source that passed the plausibility check
        ("vci", "satellite_remote_sensing"),
        ("rain_annual", "4_3_mean_annual_precipitation_master"),
        ("runoff_depth_daily", "runoff_variables_master"),
    ],
)
def test_canonical_choices(reg: Registry, var: str, table: str) -> None:
    src = reg.canonical_source(var)
    assert src is not None and src[0].table == table


@pytest.mark.parametrize(
    "var",
    [
        "evi",
        "savi",
        "msavi2",
        "vpi",
        "asi",
        "svi",
        "ndii",
        "river_discharge",
        "ssi",
        "gw_level",
        "mar_volume",
        "streamflow_anomaly",
        "cwr",
        "vpd",
        "peak_flow",
        "runoff_volume_monthly",
        "catchment_area",
        "wind_2m",
    ],
)
def test_withheld_variables_are_unavailable(reg: Registry, var: str) -> None:
    assert reg.canonical_source(var) is None
    assert reg.withheld_reasons(var), f"{var} should carry a withheld reason"


def test_whole_5_1_source_is_withheld(reg: Registry) -> None:
    ds = reg.datasets["5.1_primary_satellite_vegetation_variables_sansa_master"]
    assert all(c.role == "withheld" for c in ds.columns.values())


def test_withheld_tables_4_4_4_5_4_6(reg: Registry) -> None:
    for t in (
        "4_4_potential_evaporation_daily_master",
        "4_5_irrigation_demand_daily_master",
        "4_6_streamflow_and_climate_linkage_annual _master",
    ):
        assert all(c.role == "withheld" for c in reg.datasets[t].columns.values()), t


def test_duplicates_listed(reg: Registry) -> None:
    d = reg.duplicates()
    assert "meteorological_drought_master.spi_6" in d["spi_6"]["duplicates"]
    assert d["spi_6"]["canonical"] == [
        "6.2_rainfall_derived_drought_indices_master.SPI_6_Medium_Term_Hydrological_Impacts"
    ]


def test_sql_expressions(reg: Registry) -> None:
    assert (
        reg.datasets["6.2_rainfall_derived_drought_indices_master"].date_sql()
        == 'make_date("Year"::int, "Month"::int, 1)'
    )
    runoff = reg.datasets["runoff_variables_master"]
    assert runoff.column_sql("Daily_Surface_Runoff_Volume_m3_day") == (
        '("Daily_Surface_Runoff_Volume_m3_day" / 6500000000.0 * 1000.0)'
    )
    assert reg.datasets["6.1_core_rainfall_variables_master"].column_sql("Seasonal_Total_OND_mm") == (
        "NULLIF(\"Seasonal_Total_OND_mm\", '')::double precision"
    )


def _broken(tmp_path: Path, mutate: object) -> Path:
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    mutate(raw)  # type: ignore[operator]
    p = tmp_path / "datasets.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


def test_rejects_two_canonical_sources(tmp_path: Path) -> None:
    def m(raw: dict) -> None:  # type: ignore[type-arg]
        raw["datasets"]["meteorological_drought_master"]["columns"]["spi_6"]["role"] = "canonical"

    with pytest.raises(ValueError, match="canonical sources"):
        load_registry(_broken(tmp_path, m))


def test_rejects_withheld_without_reason(tmp_path: Path) -> None:
    def m(raw: dict) -> None:  # type: ignore[type-arg]
        del raw["datasets"]["runoff_variables_master"]["columns"]["Curve_Number_CN"]["reason"]

    with pytest.raises(ValueError, match="reason"):
        load_registry(_broken(tmp_path, m))


def test_rejects_unknown_variable(tmp_path: Path) -> None:
    def m(raw: dict) -> None:  # type: ignore[type-arg]
        raw["datasets"]["runoff_variables_master"]["columns"]["Curve_Number_CN"]["var"] = "nope"

    with pytest.raises(ValueError, match="unknown variable"):
        load_registry(_broken(tmp_path, m))


@pytest.mark.parametrize("bad", ['x"; DROP TABLE t; --', "a'b", "x\ny", ""])
def test_ident_rejects_injection(bad: str) -> None:
    with pytest.raises(ValueError):
        ident(bad)


def test_ident_quotes_registry_names() -> None:
    assert (
        ident("4_6_streamflow_and_climate_linkage_annual _master")
        == '"4_6_streamflow_and_climate_linkage_annual _master"'
    )
    assert ident("5.1_primary_satellite_vegetation_variables_sansa_master").startswith('"5.1_')
