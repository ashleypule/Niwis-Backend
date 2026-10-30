"""Labels and units from database column names — the one mapping table for the database view.

The column name is the source of truth (docs/CATEGORY_CATALOGUE.md):
- unit = the column's suffix, looked up in `SUFFIXES` (longest match, case-insensitive); no suffix → no unit;
- label = the name without the unit suffix, underscores → spaces, words capitalised, acronyms as written
  (`ACRONYMS`), e.g. `daily_rainfall_mm_day` → "Daily Rainfall (mm/day)",
  `SPI_3_Short_Term_Agricultural_Drought` → "SPI 3 – Short Term Agricultural Drought".

`LABEL_OVERRIDES` may only fix a parsing failure (the name carries no unit, or the wrong one) or unit typography, and
every entry says why. Variables are never renamed to friendlier wording. The §9–§16 tables (2026-10-19) name many
columns after the parameter only ("Turbidity", "pH"): their units come from the spec table for that section, listed per
column in `SPEC_UNITS` with the section.

`repair_unit` maps unit text stored in the database (long-format tables have a `unit` column) to clean typography,
including mojibake: UTF-8 "m³" decoded as Windows-1252 ("mÂ³") or replaced by U+FFFD ("m�") by a viewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# suffix (lower case, including the leading underscore) → unit; None = dimensionless (the suffix is dropped)
_SUFFIXES: dict[str, str | None] = {
    "_million_m3_month": "Mm³/month",
    "_million_m3_yr": "Mm³/year",
    "_m3_ha_season": "m³/ha/season",
    "_l_cap_day": "L/capita/day",
    "_degrees": "°",
    "_m3_m3": "m³/m³",
    "_m3_yr": "m³/year",
    "_m3_h": "m³/h",
    "_m2_day": "m²/day",
    "_m_day": "m/day",
    "_amsl_m": "m amsl",
    "_m_amsl": "m amsl",
    "_t_day": "t/day",
    "_mg_m3": "mg/m³",
    "_ugl": "µg/L",
    "_m3": "m³",
    "_m": "m",
    "_percent_per_yr": "%/year",
    "_hours": "h",
    "_million_m3": "Mm³",
    "_mm_per_wet_day": "mm/wet day",
    "_mm_season": "mm/season",
    "_mm_dekad": "mm/dekad",
    "_mm_month": "mm/month",
    "_mm_day": "mm/day",
    "_mm_yr": "mm/year",
    "_mm_h": "mm/h",
    "_mm": "mm",
    "_days_month": "days/month",
    "_days": "days",
    "_m3_year": "m³/year",
    "_m3_day": "m³/day",
    "_m3_s": "m³/s",
    "_m3s": "m³/s",
    "_m3m3": "m³/m³",
    "_km2": "km²",
    "_wm2": "W/m²",
    "_ms": "m/s",
    "_kpa": "kPa",
    "_m_bgl": "m bgl",
    "_pct_fsc": "% FSC",
    "_fsc_pct": "% FSC",
    "_kwh_ml": "kWh/Ml",
    "_mm3": "Mm³",
    "_percent": "%",
    "_pct": "%",
    "_c": "°C",
    "_dimensionless": None,
    "_standardised": None,
}
SUFFIXES: tuple[tuple[str, str | None], ...] = tuple(sorted(_SUFFIXES.items(), key=lambda kv: -len(kv[0])))

# acronyms (matched case-insensitively on a whole word) → how they are written in labels
_ACRONYM_LIST = (
    "SPI",
    "SPEI",
    "SSI",
    "SGI",
    "RAI",
    "SVI",
    "NDVI",
    "NDWI",
    "NDII",
    "EVI",
    "SAVI",
    "MSAVI2",
    "VCI",
    "VHI",
    "TCI",
    "VPI",
    "ASI",
    "CDD",
    "CWD",
    "PRCPTOT",
    "SDII",
    "MAR",
    "GDD",
    "CN",
    "LST",
    "TWS",
    "GRACE",
    "FDC",
    "FSC",
    "CV",
    "MK",
    "ENSO",
    "CHIRPS",
    "TAMSAT",
    "CWR",
    "VPD",
    "AET",
    "PET",
    "ET",
    "DJF",
    "MAM",
    "JJA",
    "SON",
    "OND",
    "JFM",
    "AMJ",
    "JAS",
    "SANSA",
    "SAWS",
    "NDWI",
    "HSG",
    "TWI",
    "WTF",
    "MAP",
    "MAE",
    "WRM",
    "THMS",
    "TDS",
    "TN",
    "TP",
    "EC",
    "DO",
    "TSS",
    "NH3",
    "ASPT",
    "FIBI",
    "SASS5",
    "BOD5",
    "COD",
    "TIN",
)
ACRONYMS: dict[str, str] = {w.lower(): w for w in _ACRONYM_LIST} | {"et0": "ET₀", "thms": "THMs", "ph": "pH"}
SMALL_WORDS = {"of", "and", "from", "per", "the", "to", "in", "on"}
# standardised indices share one "index" axis; vegetation / water indices (−1 … +1) share another (axis rule A3)
STANDARDISED_INDICES = {"SPI", "SPEI", "SSI", "SGI", "RAI", "SVI"}
SPECTRAL_INDICES = {"NDVI", "NDWI", "NDII", "EVI", "SAVI", "MSAVI2"}


@dataclass(frozen=True)
class Override:
    unit: str | None
    why: str


# (table, column) → corrected unit. Only for parsing failures and unit typography; each says why.
LABEL_OVERRIDES: dict[tuple[str, str], Override] = {
    ("satellite_remote_sensing", "lst"): Override("°C", "name has no unit suffix; spec §3.2 gives LST in °C"),
    ("satellite_remote_sensing", "vci"): Override("%", "name has no unit suffix; spec §3.2: VCI 0–100 %"),
    ("satellite_remote_sensing", "tci"): Override("%", "name has no unit suffix; spec §3.2: TCI 0–100 %"),
    ("satellite_remote_sensing", "vhi"): Override("%", "name has no unit suffix; spec §3.2: VHI 0–100 %"),
    ("satellite_remote_sensing", "rootzone_soil_moisture"): Override(
        "m³/m³", "name has no unit suffix; spec §3.2: root-zone soil moisture in m³/m³"
    ),
    ("satellite_remote_sensing", "surface_soil_moisture"): Override(
        "m³/m³", "name has no unit suffix; spec §3.2: surface soil moisture in m³/m³"
    ),
    ("5.2_meteorological_drivers_saws_master", "Growing_Degree_Days_GDD_C"): Override(
        "°C·days",
        "suffix `_C` reads as °C, but the values are degree-day sums: Σ max(Tavg − 10 °C, 0), checked against the "
        "daily temperatures",
    ),
}


_SPEC = {
    "8": "spec §8.1",
    "9.1": "spec §9.1",
    "9.3": "spec §9.3",
    "11.1": "spec §11.1",
    "11.2": "spec §11.2",
    "11.3": "spec §11.3",
    "12.1": "spec §12.1",
    "12.2": "spec §12.2",
    "12.3": "spec §12.3",
    "13.1": "spec §13.1",
    "13.2": "spec §13.2",
    "14.1": "spec §14.1",
    "15.2": "spec §15.2",
    "15.3": "spec §15.3",
    "15.1": "spec §15.1",
    "18.1": "spec §18.1",
    "18.2": "spec §18.2",
    "18.3": "spec §18.3",
    "19.1": "spec §19.1",
    "19.2": "spec §19.2",
    "19.3": "spec §19.3",
}


def _spec_units(table: str, sec: str, units: dict[str, str]) -> dict[tuple[str, str], Override]:
    return {(table, c): Override(u, f"name has no unit; {_SPEC[sec]} gives {u}") for c, u in units.items()}


# (table, column) → unit from the spec table of the column's section, for columns named after the parameter only.
SPEC_UNITS: dict[tuple[str, str], Override] = {
    **_spec_units(
        "9_1_hydrological_station_master",
        "9.1",
        {
            "daily_mean_flow": "m³/day",
            "river_discharge_q": "m³/s",
            "flow_velocity": "m/s",
            "stage_height": "m",
            "monthly_mean_flow": "m³/month",
            "suspended_sediment_concentration": "mg/L",
            "peak_instantaneous_flow": "m³/s",
        },
    ),
    **_spec_units(
        "11_1_hydrogeological",
        "11.1",
        {
            "Depth to water table": "m bgl",
            "Aquifer extent (map area)": "km²",
            "Aquifer saturated thickness": "m",
            "Depth to bedrock / aquifer base": "m bgl",
            "Dynamic (renewable) reserve": "Mm³/year",
            "Permeability / hydraulic conductivity": "m/day",
            "Porosity (total and effective)": "%",
            "Static reserve volume": "Mm³",
            "Transmissivity": "m²/day",
        },
    ),
    **_spec_units(
        "11.2_recharge_and_discharge",
        "11.2",
        {
            "Baseflow discharge": "m³/s",
            "Evapotranspiration": "mm",
            "Spring discharge": "L/s",
            "Direct recharge": "mm/year",
            "Indirect recharge": "m³/year",
            "Rainfall infiltration": "mm/year",
        },
    ),
    **_spec_units(
        "11.3_aquifer_sustainability",
        "11.3",
        {
            "Aquifer depletion rate": "m/year",
            "Borehole yield": "m³/h",
            "Exploitation index": "%",
            "Total abstraction": "m³/year",
        },
    ),
    **_spec_units(
        "12.1_physical_water_quality_master",
        "12.1",
        {
            "Temperature": "°C",
            "Turbidity": "NTU",
            "Colour": "Hazen",
            "Electrical Conductivity (EC)": "mS/m",
            "Total Dissolved Solids (TDS)": "mg/L",
            "Total Suspended Solids (TSS)": "mg/L",
            "Salinity": "mg/L",
        },
    ),
    **_spec_units(
        "12.2_chemical_water_quality_master",
        "12.2",
        {
            "Dissolved Oxygen (DO)": "mg/L",
            "BOD5": "mg/L",
            "COD": "mg/L",
            "Ammonia (NH3-N)": "mg/L",
            "Nitrate (NO3-N)": "mg/L",
            "Nitrite (NO2-N)": "mg/L",
            "Total Phosphorus (TP)": "mg/L",
            "Orthophosphate (PO43-)": "mg/L",
            "Lead/Cadmium/Chromium": "µg/L",
            "Arsenic/Mercury": "µg/L",
            "Iron/Manganese": "mg/L",
            "TOC": "mg/L",
            "Sulphate": "mg/L",
            "Chloride": "mg/L",
            "Fluoride": "mg/L",
        },
    ),
    **_spec_units(
        "12.3_biological_water_quality_master",
        "12.3",
        {
            "E. coli": "cfu/100 mL",
            "Total coliforms": "cfu/100 mL",
            "Chlorophyll-a": "µg/L",
            "Blue-green algae": "cells/mL",
            "Microcystin": "µg/L",
        },
    ),
    **_spec_units(
        "13.1_wastewater_effluent_quality_master",
        "13.1",
        {
            "temperature": "°C",
            "turbidity": "NTU",
            "ec_salinity": "mS/m",
            "tss": "mg/L",
            "do": "mg/L",
            "BOD5": "mg/L",
            "COD": "mg/L",
            "Ammonia_NH3_N": "mg/L",
            "Total_Inorganic_Nitrogen": "mg/L",
            "Total_Phosphorus": "mg/L",
            "Heavy_Metals": "µg/L",
            "E_coli": "cfu/100 mL",
            "Faecal_coliforms": "cfu/100 mL",
            "Helminth_eggs": "eggs/L",
        },
    ),
    **_spec_units(
        "13.2_WWTW_operational_master",
        "13.2",
        {
            "chlorine_dosing": "mg/L",
            "design_capacity": "m³/day",
            "hydraulic_loading_rate": "%",
            "hydraulic_retention_time": "h",
            "inflow_infiltration": "m³/day",
            "influent_flow_rate": "m³/day",
            "power_consumption": "kWh/day",
            "sludge_production": "m³/day",
        },
    ),
    **_spec_units(
        "14.1_SANS_241_compliance_master",
        "14.1",
        {
            "Turbidity_Value": "NTU",
            "Colour_Value": "TCU",
            "Temperature_Value": "°C",
            "E_coli_Value": "cfu/100 mL",
            "Total_Coliforms_Value": "cfu/100 mL",
            "EC_Value": "mS/m",
            "Free_Chlorine_Value": "mg/L",
            "Nitrate_Value": "mg/L",
            "Fluoride_Value": "mg/L",
            "Arsenic_Value": "mg/L",
            "Lead_Value": "mg/L",
            "Cyanide_Value": "mg/L",
            "Total_THMs_Value": "mg/L",
        },
    ),
    **_spec_units(
        "15.2_chemical_RWQO_variables_master",
        "15.2",
        {
            "DO_Value": "mg/L",
            "EC_Target_Value": "mS/m",
            "Nutrient_Value_TN": "mg/L",
            "Nutrient_Value_TP": "mg/L",
            "Salinity_Value_TDS": "mg/L",
        },
    ),
    **_spec_units(
        "15.3_hydrological_RWQO_variables_master", "15.3", {"Low_Flow_Value": "m³/s", "Dilution_Value": "m³/s"}
    ),
    **_spec_units("15.1_ecological_variables_master", "15.1", {"ecological_reserve_q": "m³/s"}),
}
SPEC_UNITS.update(
    _spec_units(
        "18_1_water_access_and_coverage_master",
        "18.1",
        {
            "Total Households": "households",
            "At or Above RDP Households": "households",
            "At or Above RDP Percentage": "%",
            "Piped Infrastructure Households": "households",
            "Reliable Water Households": "households",
            "Reliable Water Percentage": "%",
            "Households Below RDP / Backlog (#)": "households",
            "Households Below RDP / Backlog (%)": "%",
            "Annual New Connections Installed (#/year)": "connections/year",
            "Piped Water Inside Dwelling (#)": "households",
            "Piped Water On Site / Yard Tap (#)": "households",
            "Communal Standpipe < 200m (#)": "households",
            "Piped Water Inside Dwelling (%)": "%",
            "Piped Water On Site / Yard Tap (%)": "%",
            "Communal Standpipe < 200m (%)": "%",
            "Distance to Nearest Water Point for Unserved HH (km)": "km",
            "Schools with Piped Water (%)": "%",
            "Clinics with Piped Water (%)": "%",
        },
    )
)
SPEC_UNITS.update(
    _spec_units(
        "18_2_infrastructure_operational_master",
        "18.2",
        {
            "System input volume (litres)": "L/year",
            "Revenue water (litres)": "L/year",
            "Non-Revenue water (litres)": "L/year",
            "Water Losses (litres)": "L/year",
            "Daily_Water_Production_Volume_m3_day": "m³/day",
            "Physical_Losses_Real_Losses_m3_day": "m³/day",
            "Computed_NRW_Percentage": "%",
            "Revenue_Water_m3_day": "m³/day",
            "NRW_Volume_m3_day": "m³/day",
            "Commercial_Losses_m3_day": "m³/day",
            "Computed_Water_Loss_Ratio_Pct": "%",
            "Calculated_Demand_Growth_Pct": "%",
            "System_Supply_Pressure_kPa_bar": "kPa",
            "Daily_Unplanned_Supply_Interruptions_events_per_day": "events/day",
            "Average_Duration_Of_Outages_hours_per_event": "h",
            "Pump_Station_Energy_Consumption_kWh_Ml": "kWh/Ml",
            "Load_Shedding_Induced_Interruptions_hours_per_day": "h/day",
            "Pipe_Failure_Rate_breaks_per_100km_year": "breaks/100 km/year",
        },
    )
)
SPEC_UNITS.update(
    _spec_units(
        "18_3_sansa_eo_support_for_services_masters",
        "18.3",
        {
            "Settlement_Extent_Built_km2": "km²",
            "Informal_Settlements_Extent_km2": "km²",
            "Water_Infrastructure_Reservoir_Extent_km2": "km²",
            "Drought_Severity_PDSI": "index",
            "Climatic_Water_Deficit_mm": "mm/year",
            "Catchment_Precipitation_mm": "mm",
        },
    )
)
SPEC_UNITS.update(
    _spec_units(
        "19_1_raw_water_tariff_master",
        "19.1",
        {
            "Raw water abstraction tariff (R/m³)": "R/m³",
            "Drought-season surcharge (R/m³)": "R/m³",
            "Capital investment recovery charge (R/m³)": "R/m³",
            "Reservoir storage % (scarcity trigger) (% FSC)": "% FSC",
            "Inter-basin transfer cost component (R/Ml)": "R/Ml",
            "Energy cost per Ml transferred (R/Ml)": "R/Ml",
            "MAR_demand_ratio": "—",
        },
    )
)
SPEC_UNITS.update(
    _spec_units(
        "19_2_municipal_water_tariff_master",
        "19.2",
        {
            "Block tariff rates (R/kL per consumption block)": "R/kL",
            "6 kL/month free allocation status (L/household/month)": "L/household/month",
            "Average treatment cost per kL produced (R/kL)": "R/kL",
            "Electricity cost for pumping and treatment (R/kWh)": "kWh/Ml",
            "Infrastructure replacement and maintenance cost (R/year)": "R/year",
            "CPI and PPI affecting operational costs (%)": "%",
        },
    )
)
SPEC_UNITS.update(
    _spec_units(
        "19_3_water_quality_cost_master",
        "19.3",
        {
            "Source water (Turbidity and contamination)": "R/year",
            "Chemical dosing cost": "R/kL",
            "Algal bloom treatment": "R/year",
            "WWTW compliance penalty": "R/year",
        },
    )
)
LABEL_OVERRIDES.update(SPEC_UNITS)
LABEL_OVERRIDES.update(
    {
        ("8_1_reservoir_operational_master", "pct_fsc"): Override(
            "% FSC", "the name is the unit itself (`pct_fsc`); spec §8.1 gives % of full supply capacity"
        ),
        ("10_1_borehole_and_aquifer_master", "seasonal_water_table_fluctuation_m"): Override(
            "m/season", "suffix `_m` reads as m; the unit column and spec §10.1 give m/season"
        ),
        ("10_1_borehole_and_aquifer_master", "hydraulic_head_m_amsl"): Override(
            "m amsl",
            "the unit column says 'm bgl', but the values (448–1 638 m) are elevations, as the name says: a water "
            "level 1 638 m below ground is impossible in South Africa's aquifers",
        ),
    }
)
# 0/1 compliance flags are served as % compliant (100 = compliant; scope values = share of the area compliant)
BOOL_PCT = Override(
    "%", "a yes / no compliance flag, served as % compliant (100 = compliant; scope values = % of the area)"
)

# unit text stored in the database → clean unit. Mojibake seen in exports: UTF-8 bytes decoded as Windows-1252 ("Â³")
# or replaced by U+FFFD ("�"); the 10_1 values themselves are valid UTF-8 (checked 2026-10-20).
UNIT_REPAIRS: dict[str, str] = {
    "m\ufffd/year": "m³/year",
    "m\ufffd/h": "m³/h",
    "m\ufffd/day": "m²/day",  # only transmissivity is stored per day with a superscript (m²/day)
    "mÂ³/year": "m³/year",
    "mÂ³/h": "m³/h",
    "mÂ²/day": "m²/day",
    "m3/year": "m³/year",
    "m3/h": "m³/h",
    "m2/day": "m²/day",
    "dimensionless": "",
}


def repair_unit(stored: str | None) -> str | None:
    """Clean unit text stored in a long-format table's `unit` column (`UNIT_REPAIRS`); empty → None."""
    if stored is None:
        return None
    text = stored.strip()
    fixed = UNIT_REPAIRS.get(text, text)
    return fixed or None


def split_unit(column: str) -> tuple[str, str | None]:
    """(name without the unit suffix, unit) — `("daily_rainfall", "mm/day")`; no recognised suffix → (column, None)."""
    low = column.lower()
    for suffix, unit in SUFFIXES:
        if low.endswith(suffix) and len(low) > len(suffix):
            return column[: -len(suffix)], unit
    return column, None


def _word(token: str, first: bool) -> str:
    low = token.lower()
    if low in ACRONYMS:
        return ACRONYMS[low]
    if low in SMALL_WORDS and not first:
        return low
    return token[:1].upper() + token[1:]  # keep the rest as written (LowFlow, LandCover, 10day)


def name_of(base: str) -> str:
    """Label words for a column name without its unit suffix.

    A standardised index followed by its time scale and a description gets a dash:
    `SPI_3_Short_Term_Agricultural_Drought` → "SPI 3 – Short Term Agricultural Drought"; `spi_3` → "SPI 3".
    """
    tokens = [t for t in re.split(r"[_\s]+", base.strip()) if t]
    words = [_word(t, i == 0) for i, t in enumerate(tokens)]
    if words and words[0] in STANDARDISED_INDICES:
        head = words[:2] if len(words) > 1 and tokens[1].isdigit() else words[:1]
        rest = words[len(head) :]
        if len(rest) >= 2:
            return " ".join(head) + " – " + " ".join(rest)
    return " ".join(words)


def with_unit(name: str, unit: str | None) -> str:
    return f"{name} ({unit})" if unit else name


def column_label(table: str, column: str) -> tuple[str, str | None, str | None]:
    """(name, unit, override reason) for a column, applying `LABEL_OVERRIDES`."""
    base, unit = split_unit(column)
    ov = LABEL_OVERRIDES.get((table, column))
    if ov is not None:
        return name_of(base), ov.unit, ov.why
    return name_of(base), unit, None


def index_family(name: str) -> str | None:
    """`standardised` / `spectral` for unitless index columns (shared axes), else None."""
    first = name.split(" ", 1)[0]
    if first in STANDARDISED_INDICES:
        return "standardised"
    if first in SPECTRAL_INDICES:
        return "spectral"
    return None
