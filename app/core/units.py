"""Labels and units from database column names — the one mapping table for the database view.

The column name is the source of truth (docs/CATEGORY_CATALOGUE.md):
- unit = the column's suffix, looked up in `SUFFIXES` (longest match, case-insensitive); no suffix → no unit;
- label = the name without the unit suffix, underscores → spaces, words capitalised, acronyms as written
  (`ACRONYMS`), e.g. `daily_rainfall_mm_day` → "Daily Rainfall (mm/day)",
  `SPI_3_Short_Term_Agricultural_Drought` → "SPI 3 – Short Term Agricultural Drought".

`LABEL_OVERRIDES` may only fix a parsing failure (the name carries no unit, or the wrong one) or unit typography, and
every entry says why. Variables are never renamed to friendlier wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# suffix (lower case, including the leading underscore) → unit; None = dimensionless (the suffix is dropped)
_SUFFIXES: dict[str, str | None] = {
    "_million_m3_month": "Mm³/month",
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
)
ACRONYMS: dict[str, str] = {w.lower(): w for w in _ACRONYM_LIST} | {"et0": "ET₀"}
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
