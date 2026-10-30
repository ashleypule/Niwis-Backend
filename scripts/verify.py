"""Spot-check API values against direct SQL on the real database (read-only) and write docs/VERIFICATION.md.

Each check: an API request (served in-process from the live database), a JSON path to the value, an independent SQL
query, and a tolerance. The SQL is written from scratch here — it does not reuse the API's query code.

Usage: python scripts/verify.py [module ...]      (default: all modules with checks)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import Settings  # noqa: E402
from app.core.spatial import get_spatial  # noqa: E402
from app.main import create_app  # noqa: E402

OUT = ROOT / "docs" / "VERIFICATION.md"


@dataclass
class Check:
    module: str
    what: str
    path: str
    pick: Callable[[Any], Any]
    sql: str
    tol: float = 0.05  # absolute tolerance (API values are rounded)
    sql_post: Callable[[list[tuple[Any, ...]]], Any] = lambda rows: rows[0][0]
    # withheld values: the check passes when the API value is null AND the SQL evidence still shows the failure
    expect_null: Callable[[Any], bool] | None = None


def kpi(kid: str) -> Callable[[Any], Any]:
    return lambda b: next(k["value"] for k in b["kpis"] if k["id"] == kid)


def _province_weighted(rows: list[tuple[Any, ...]], province: str) -> float:
    """Area-weight district rows (district_name, value) with the boundary areas — the only non-SQL input."""
    from app.core import districts

    areas = get_spatial().district_areas() or {}
    num = den = 0.0
    for name, v in rows:
        d = districts.resolve(name)
        if d and d.province == province and v is not None:
            num += areas[d.code] * float(v)
            den += areas[d.code]
    return num / den


def _area_share_below(rows: list[tuple[Any, ...]], threshold: float) -> float:
    """Share (%) of national area in districts whose value is below `threshold`."""
    from app.core import districts

    areas = get_spatial().district_areas() or {}
    total = below = 0.0
    for name, v in rows:
        d = districts.resolve(name)
        if d and v is not None:
            total += areas[d.code]
            below += areas[d.code] if float(v) < threshold else 0.0
    return below / total * 100


def _times_area(code: str) -> Callable[[list[tuple[Any, ...]]], Any]:
    """Multiply a depth (mm) from SQL by the district's boundary area (km²) / 1000 → Mm³."""

    def f(rows: list[tuple[Any, ...]]) -> Any:
        areas = get_spatial().district_areas() or {}
        return round(float(rows[0][0]) * areas[code] / 1000, 0)

    return f


def series_at(key: str, x: str) -> Callable[[Any], Any]:
    """Value of series `key` at x (e.g. "2025-01-01")."""
    return lambda b: next(p[1] for s in b["series"] if s["key"] == key for p in s["data"] if p[0] == x)


def field_value(label_start: str) -> Callable[[Any], Any]:
    """Value of the first entity-card field whose label starts with `label_start`."""
    return lambda b: next(f["value"] for f in b["fields"] if f["label"].startswith(label_start))


def series_last(key: str) -> Callable[[Any], Any]:
    """Last non-null value of series `key`."""
    return lambda b: [p[1] for s in b["series"] if s["key"] == key for p in s["data"] if p[1] is not None][-1]


def category(series_key: str, index: int) -> Callable[[Any], Any]:
    return lambda b: next(s["values"][index] for s in b["series"] if s["key"] == series_key)


def row(row_id: str, key: str) -> Callable[[Any], Any]:
    return lambda b: next(r[key] for r in b["rows"] if r["id"] == row_id)


def at_as_of(b: Any) -> Any:
    """The pos / neg anomaly bar at the response's asOf month."""
    return next(p[1] for s in b["series"] for p in s["data"] if p[0] == b["asOf"] and p[1] is not None)


def _spec_class(v: float) -> str:
    """Spec §3.4 class of an SPI value (rounded to 2 dp, <= as published) — written here, not imported."""
    r = round(v + 0.0, 2)
    for lim, cls in ((-2.0, "D4"), (-1.6, "D3"), (-1.3, "D2"), (-0.8, "D1"), (-0.5, "D0")):
        if r <= lim:
            return cls
    return "None"


def _severe_share_year(rows: list[tuple[Any, ...]]) -> float:
    """(district, month, SPI-3) rows of one year → mean over the 12 months of the % of national area in D2–D4."""
    from app.core import districts

    areas = get_spatial().district_areas() or {}
    by_month: dict[Any, list[tuple[float, str]]] = {}
    for name, month, v in rows:
        d = districts.resolve(name)
        if d and v is not None:
            by_month.setdefault(month, []).append((areas[d.code], _spec_class(float(v))))
    shares = [
        sum(a for a, c in xs if c in ("D2", "D3", "D4")) / sum(a for a, _ in xs) * 100 for xs in by_month.values()
    ]
    assert len(shares) == 12
    return round(sum(shares) / 12, 1)


def _headline_rank(province: str | None) -> Callable[[list[tuple[Any, ...]]], Any]:
    """(district, SPI-6) rows → rank of the worst class that, with all worse classes, covers ≥ 20 % of the area."""

    def f(rows: list[tuple[Any, ...]]) -> Any:
        from app.core import districts

        areas = get_spatial().district_areas() or {}
        got: dict[str, float] = {}
        for name, v in rows:
            d = districts.resolve(name)
            if d and v is not None and (province is None or d.province == province):
                got[_spec_class(float(v))] = got.get(_spec_class(float(v)), 0.0) + areas[d.code]
        total, cum = sum(got.values()), 0.0
        for rank, cls in ((5, "D4"), (4, "D3"), (3, "D2"), (2, "D1"), (1, "D0")):
            cum += got.get(cls, 0.0)
            if cum / total * 100 >= 20:
                return rank
        return 0

    return f


def _weighted_ratio(rows: list[tuple[Any, ...]]) -> float:
    """(district_name, numerator, denominator) rows → Σ area·num / Σ area·den with the boundary areas."""
    from app.core import districts

    areas = get_spatial().district_areas() or {}
    num = den = 0.0
    for name, a, b in rows:
        d = districts.resolve(name)
        if d and a is not None and b is not None:
            num += areas[d.code] * float(a)
            den += areas[d.code] * float(b)
    return num / den


def _national_flow_mean(rows: list[tuple[Any, ...]]) -> float:
    """(day, district_name, depth mm) rows → mean over the days of Σ districts depth × area × 1000 / 86 400 (m³/s)."""
    from app.core import districts

    areas = get_spatial().district_areas() or {}
    per_day: dict[Any, float] = {}
    for day, name, depth in rows:
        d = districts.resolve(name)
        if d and depth is not None:
            per_day[day] = per_day.get(day, 0.0) + float(depth) * areas[d.code] * 1000 / 86400
    return sum(per_day.values()) / len(per_day)


def _flow_m3s(code: str) -> Callable[[list[tuple[Any, ...]]], Any]:
    """Mean daily model volume (m³/day, 6 500 km² basis) → depth (mm/day) × real area → m³/s."""

    def f(rows: list[tuple[Any, ...]]) -> Any:
        areas = get_spatial().district_areas() or {}
        depth_mm = float(rows[0][0]) / 6.5e9 * 1000
        return round(depth_mm * areas[code] * 1000 / 86400, 2)

    return f


LATEST_COMPLETE_MONTH = (
    """(date_trunc('month', (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master"))"""
    """ - interval '1 month')::date"""
)


CHECKS: list[Check] = [
    Check(
        "rainfall",
        "Total rainfall, last 12 months, City of Cape Town (mm)",
        "/api/v1/rainfall/summary?municipality=CPT",
        kpi("total"),
        """SELECT round(sum("Daily_Rainfall_Total_mm")::numeric, 1)
FROM "6.1_core_rainfall_variables_master"
WHERE "District_Name" = 'City of Cape Town'
  AND "Date"::date BETWEEN (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master") - interval '1 year' + interval '1 day'
                       AND (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master")""",
    ),
    Check(
        "rainfall",
        "Rainfall anomaly, last 12 months, City of Cape Town (%) — normal = Σ (monthly total − stored anomaly), pro-rated by days",
        "/api/v1/rainfall/summary?municipality=CPT",
        kpi("anomaly"),
        """WITH win AS (SELECT (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master") AS e),
days AS (SELECT d::date AS d FROM win, generate_series(e - interval '1 year' + interval '1 day', e, interval '1 day') d),
norm AS (SELECT "Month" AS m, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") AS n
         FROM "6.1_core_rainfall_variables_master" WHERE "District_Name" = 'City of Cape Town' GROUP BY 1),
nwin AS (SELECT sum(n / extract(day FROM (date_trunc('month', d) + interval '1 month' - interval '1 day'))) AS n
         FROM days JOIN norm ON norm.m = extract(month FROM d)),
p AS (SELECT sum("Daily_Rainfall_Total_mm") AS p FROM "6.1_core_rainfall_variables_master", win
      WHERE "District_Name" = 'City of Cape Town' AND "Date"::date BETWEEN e - interval '1 year' + interval '1 day' AND e)
SELECT round(((p - n) / n * 100)::numeric, 1) FROM p, nwin""",
    ),
    Check(
        "rainfall",
        "Max daily rainfall, last 12 months, national (mm)",
        "/api/v1/rainfall/summary",
        kpi("maxDaily"),
        """SELECT round(max("Daily_Rainfall_Total_mm")::numeric, 1) FROM "6.1_core_rainfall_variables_master"
WHERE "Date"::date > (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master") - interval '1 year'""",
    ),
    Check(
        "rainfall",
        "SPI-6, Namakwa (DC6), latest complete month — map layer spi6",
        "/api/v1/rainfall/map?layer=spi6",
        lambda b: b["values"]["DC6"],
        """SELECT round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric, 2) FROM "6.2_rainfall_derived_drought_indices_master"
WHERE "District_Name" = 'Namakwa' AND make_date("Year"::int, "Month"::int, 1) = date_trunc('month', (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master")) - interval '1 month'""",
        tol=0.005,
    ),
    Check(
        "rainfall",
        "SPI-6, Western Cape, latest complete month — area-weighted from the 6 WC districts",
        "/api/v1/rainfall/summary?province=WC",
        kpi("spi6"),
        """SELECT "District_Name", "SPI_6_Medium_Term_Hydrological_Impacts" FROM "6.2_rainfall_derived_drought_indices_master"
WHERE make_date("Year"::int, "Month"::int, 1) = date_trunc('month', (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master")) - interval '1 month'""",
        tol=0.005,
        sql_post=lambda rows: round(_province_weighted(rows, "WC"), 2),
    ),
    Check(
        "drought",
        "SPI-3, KwaZulu-Natal, latest complete month — area-weighted from the 11 KZN districts",
        "/api/v1/drought/summary?province=KZN",
        kpi("spi3"),
        """SELECT "District_Name", "SPI_3_Short_Term_Agricultural_Drought" FROM "6.2_rainfall_derived_drought_indices_master"
WHERE make_date("Year"::int, "Month"::int, 1) = date_trunc('month', (SELECT max("Date")::date FROM "6.1_core_rainfall_variables_master")) - interval '1 month'""",
        tol=0.005,
        sql_post=lambda rows: round(_province_weighted(rows, "KZN"), 2),
    ),
    Check(
        "drought",
        "Dam storage, national (% FSC) — withheld: null while national storage stays frozen since Dec 2021 "
        "(SQL = number of distinct national totals since then; 1 = frozen)",
        "/api/v1/drought/summary",
        kpi("storage"),
        """SELECT count(DISTINCT round(v::numeric, 3)) FROM (SELECT date, sum(dam_storage_million_m3) AS v
FROM hydrological_drought_master WHERE date >= '2021-12-01' GROUP BY 1) s""",
        expect_null=lambda v: v == 1,
    ),
    Check(
        "drought",
        "VCI, Namakwa (DC6), latest month — map layer vci (satellite_remote_sensing)",
        "/api/v1/drought/map?layer=vci",
        lambda b: b["values"]["DC6"],
        """SELECT round(vci::numeric, 1) FROM satellite_remote_sensing
WHERE district_name = 'namakwa' AND date = (SELECT max(date) FROM satellite_remote_sensing)""",
    ),
    Check(
        "drought",
        "Surface soil-moisture anomaly, Namakwa, latest month vs same month 2015–2025 (%) — the values before Mar 2015 "
        "are a constant fill and are excluded",
        "/api/v1/drought/summary?municipality=DC6",
        kpi("soilMoisture"),
        """WITH cur AS (SELECT date, surface_soil_moisture AS v FROM satellite_remote_sensing
             WHERE district_name = 'namakwa' AND date = (SELECT max(date) FROM satellite_remote_sensing)),
     clim AS (SELECT avg(surface_soil_moisture) AS n FROM satellite_remote_sensing, cur
              WHERE district_name = 'namakwa' AND extract(month FROM satellite_remote_sensing.date) = extract(month FROM cur.date)
                AND satellite_remote_sensing.date >= '2015-03-01'
                AND extract(year FROM satellite_remote_sensing.date) BETWEEN 2001 AND 2025)
SELECT round(((v - n) / n * 100)::numeric, 1) FROM cur, clim""",
    ),
    Check(
        "climate",
        "Hot days (Tmax > 35 °C), Vhembe (DC34), last full year — map layer hot-days vs count of daily Tmax > 35 °C",
        "/api/v1/climate/map?layer=hot-days",
        lambda b: b["values"]["DC34"],
        """SELECT count(*) FROM "4_1_temperature_change_master"
WHERE "District_Name" = 'Vhembe' AND "Tmax_C" > 35
  AND left("Date", 4)::int = (SELECT max(left("Date", 4)::int) - 1 FROM "4_1_temperature_change_master")""",
        tol=0.5,
    ),
    Check(
        "climate",
        "Reference ET₀, Vhembe, last full year (mm) — Σ daily et0_mm_day",
        "/api/v1/climate/summary?municipality=DC34",
        kpi("et0"),
        """SELECT round(sum(et0_mm_day)::numeric, 0) FROM meteorological_drought_master
WHERE district_name = 'Vhembe' AND year = (SELECT max(year) - 1 FROM meteorological_drought_master)""",
        tol=0.5,
    ),
    Check(
        "climate",
        "Warming trend, Vhembe, 2000–last full year (°C/decade) — Sen's slope computed in SQL (median of pairwise slopes)",
        "/api/v1/climate/map?layer=temp-trend",
        lambda b: b["values"]["DC34"],
        """WITH y AS (SELECT left("Date", 4)::int AS yr, avg("Tavg_C") AS t FROM "4_1_temperature_change_master"
           WHERE "District_Name" = 'Vhembe' GROUP BY 1
           HAVING left("Date", 4)::int < (SELECT max(left("Date", 4)::int) FROM "4_1_temperature_change_master"))
SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (b.t - a.t) / (b.yr - a.yr)) * 10)::numeric, 3)
FROM y a JOIN y b ON b.yr > a.yr""",
        tol=0.0015,
    ),
    Check(
        "climate",
        "Temperature anomaly, Vhembe, 12 complete months (°C) — mean of the stored monthly anomaly",
        "/api/v1/climate/map?layer=temp-anomaly",
        lambda b: b["values"]["DC34"],
        """WITH m AS (SELECT left("Date", 7) AS ym, max("Temperature_Anomaly_C") AS a FROM "4_1_temperature_change_master"
           WHERE "District_Name" = 'Vhembe' GROUP BY 1)
SELECT round(avg(a)::numeric, 2) FROM (SELECT a FROM m
  WHERE ym < (SELECT left(max("Date"), 7) FROM "6.1_core_rainfall_variables_master") ORDER BY ym DESC LIMIT 12) s""",
        tol=0.005,
    ),
    Check(
        "vegetation",
        "NDVI, Western Cape, latest month — area-weighted from the 6 WC districts (satellite_remote_sensing)",
        "/api/v1/vegetation/summary?province=WC",
        kpi("ndvi"),
        """SELECT district_name, ndvi FROM satellite_remote_sensing WHERE date = (SELECT max(date) FROM satellite_remote_sensing)""",
        tol=0.0006,
        sql_post=lambda rows: round(_province_weighted(rows, "WC"), 3),
    ),
    Check(
        "vegetation",
        "VHI, Namakwa (DC6), latest month — map layer vhi",
        "/api/v1/vegetation/map?layer=vhi",
        lambda b: b["values"]["DC6"],
        """SELECT round(vhi::numeric, 1) FROM satellite_remote_sensing
WHERE district_name = 'namakwa' AND date = (SELECT max(date) FROM satellite_remote_sensing)""",
    ),
    Check(
        "vegetation",
        "Area under stress (VHI < 40 %), national, latest month (% of area)",
        "/api/v1/vegetation/summary",
        kpi("stress"),
        """SELECT district_name, vhi FROM satellite_remote_sensing WHERE date = (SELECT max(date) FROM satellite_remote_sensing)""",
        sql_post=lambda rows: round(_area_share_below(rows, 40), 1),
    ),
    Check(
        "vegetation",
        "NDVI anomaly, Namakwa, latest month — NDVI minus the same-month 2001–2025 mean",
        "/api/v1/vegetation/map?layer=ndvi-anomaly",
        lambda b: b["values"]["DC6"],
        """WITH cur AS (SELECT date, ndvi FROM satellite_remote_sensing
             WHERE district_name = 'namakwa' AND date = (SELECT max(date) FROM satellite_remote_sensing))
SELECT round((cur.ndvi - avg(s.ndvi))::numeric, 3) FROM cur, satellite_remote_sensing s
WHERE s.district_name = 'namakwa' AND extract(month FROM s.date) = extract(month FROM cur.date)
  AND extract(year FROM s.date) BETWEEN 2001 AND 2025 GROUP BY cur.ndvi""",
        tol=0.0006,
    ),
    Check(
        "runoff",
        "Runoff depth, last 12 months, City of Cape Town (mm) — model volume / 6 500 km²",
        "/api/v1/runoff/summary?municipality=CPT",
        kpi("runoff"),
        """SELECT round((sum("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000)::numeric, 1) FROM runoff_variables_master
WHERE "District_Name" = 'City of Cape Town' AND "Date" BETWEEN (SELECT max("Date") FROM runoff_variables_master) - interval '1 year' + interval '1 day' AND (SELECT max("Date") FROM runoff_variables_master)""",
    ),
    Check(
        "runoff",
        "Runoff volume, last 12 months, uMgungundlovu (DC22) (Mm³) — depth × real district area (entity card)",
        "/api/v1/runoff/entities/DC22",
        lambda b: next(f["value"] for f in b["fields"] if f["label"] == "Runoff volume, last 12 months"),
        """SELECT sum("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000 FROM runoff_variables_master
WHERE "District_Name" = 'Umgungundlovu' AND "Date" BETWEEN (SELECT max("Date") FROM runoff_variables_master) - interval '1 year' + interval '1 day' AND (SELECT max("Date") FROM runoff_variables_master)""",
        tol=0.5,
        sql_post=_times_area("DC22"),
    ),
    Check(
        "runoff",
        "Runoff coefficient, last 12 months, City of Cape Town — Σ runoff / Σ rainfall (6.1)",
        "/api/v1/runoff/summary?municipality=CPT",
        kpi("coefficient"),
        """SELECT round(((SELECT sum("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000 FROM runoff_variables_master
                WHERE "District_Name" = 'City of Cape Town' AND "Date" BETWEEN (SELECT max("Date") FROM runoff_variables_master) - interval '1 year' + interval '1 day' AND (SELECT max("Date") FROM runoff_variables_master))
             / (SELECT sum("Daily_Rainfall_Total_mm") FROM "6.1_core_rainfall_variables_master"
                WHERE "District_Name" = 'City of Cape Town' AND "Date"::date BETWEEN (SELECT max("Date") FROM runoff_variables_master) - interval '1 year' + interval '1 day' AND (SELECT max("Date") FROM runoff_variables_master)))::numeric, 3)""",
        tol=0.0006,
    ),
    Check(
        "runoff",
        "Baseflow index, last 12 months, City of Cape Town — Σ baseflow × 86 400 / Σ model volume",
        "/api/v1/runoff/summary?municipality=CPT",
        kpi("bfi"),
        """SELECT round((sum("Baseflow_Component_m3s") * 86400 / sum("Daily_Surface_Runoff_Volume_m3_day"))::numeric, 2)
FROM runoff_variables_master WHERE "District_Name" = 'City of Cape Town' AND "Date" BETWEEN (SELECT max("Date") FROM runoff_variables_master) - interval '1 year' + interval '1 day' AND (SELECT max("Date") FROM runoff_variables_master)""",
        tol=0.006,
    ),
    # ---------------------------------------------------------------- integration fixes (docs/ISSUE_FIXES.md)
    Check(
        "fixes",
        "#6 Seasonal change, Namakwa (DC6), 2001–2020 DJF mean temperature (°C) — mean of monthly mean Tavg",
        "/api/v1/climate/breakdown?metric=seasonal-change&var=temperature&municipality=DC6",
        category("p1", 0),
        """WITH m AS (SELECT substr("Date", 1, 7) ym, avg("Tavg_C") v FROM "4_1_temperature_change_master"
           WHERE "District_Name" = 'Namakwa' AND substr("Date", 1, 4)::int BETWEEN 2001 AND 2020
             AND substr("Date", 6, 2)::int IN (12, 1, 2) GROUP BY 1)
SELECT round(avg(v)::numeric, 2) FROM m""",
        tol=0.005,
    ),
    Check(
        "fixes",
        "#6 Seasonal change, Namakwa (DC6), var=rainfall, 2001–2020 mean DJF rainfall (mm) — 4_3 Precip_DJF_mm",
        "/api/v1/climate/breakdown?metric=seasonal-change&var=rainfall&municipality=DC6",
        category("p1", 0),
        """SELECT round(avg("Precip_DJF_mm")::numeric, 0) FROM "4_3_mean_annual_precipitation_master"
WHERE "District_Name" = 'Namakwa' AND "Year" BETWEEN 2001 AND 2020""",
        tol=0.5,
    ),
    Check(
        "fixes",
        "#1 Vegetation indices set=vci,vhi,tci, Namakwa (DC6), latest VCI (%)",
        "/api/v1/vegetation/timeseries?metric=indices&set=vci,vhi,tci&municipality=DC6",
        series_last("vci"),
        """SELECT round(vci::numeric, 1) FROM satellite_remote_sensing
WHERE district_name = 'namakwa' AND date = (SELECT max(date) FROM satellite_remote_sensing)""",
    ),
    Check(
        "fixes",
        "#1 Vegetation indices set=ndwi,ndii, Namakwa (DC6), latest NDWI",
        "/api/v1/vegetation/timeseries?metric=indices&set=ndwi,ndii&municipality=DC6",
        series_last("ndwi"),
        """SELECT round(ndwi::numeric, 3) FROM satellite_remote_sensing
WHERE district_name = 'namakwa' AND date = (SELECT max(date) FROM satellite_remote_sensing)""",
        tol=0.0005,
    ),
    Check(
        "fixes",
        "#1 heat-days kind=cold, Joe Gqabi (DC14), frost nights 2025 — Σ Frost_Day_Flag",
        "/api/v1/climate/timeseries?metric=heat-days&kind=cold&municipality=DC14",
        series_at("annual", "2025-01-01"),
        """SELECT sum("Frost_Day_Flag") FROM "4_1_temperature_change_master"
WHERE "District_Name" = 'Joe Gqabi' AND "Date" LIKE '2025-%'""",
    ),
    Check(
        "fixes",
        "#1 extreme-rain kind=rx1, Namakwa (DC6), Rx1day 2025 (mm)",
        "/api/v1/climate/timeseries?metric=extreme-rain&kind=rx1&municipality=DC6",
        series_at("annual", "2025-01-01"),
        """SELECT round("Rx1day_mm"::numeric, 1) FROM "4_2_wet_dry_spells_master"
WHERE "District_Name" = 'Namakwa' AND "Year" = 2025""",
    ),
    Check(
        "fixes",
        "#1 national-trend var=rainfall, City of Cape Town, 2025 annual rainfall (mm) — 4_3 MAP_mm",
        "/api/v1/climate/timeseries?metric=national-trend&var=rainfall&municipality=CPT",
        series_at("annual", "2025-01-01"),
        """SELECT round("MAP_mm"::numeric, 1) FROM "4_3_mean_annual_precipitation_master"
WHERE "District_Name" = 'City of Cape Town' AND "Year" = 2025""",
    ),
    Check(
        "fixes",
        "#1 rainfall anomaly window=3, City of Cape Town, latest complete month (%) — 3-month total vs Σ normals",
        "/api/v1/rainfall/timeseries?metric=anomaly&window=3&municipality=CPT",
        at_as_of,
        f"""WITH e AS (SELECT {LATEST_COMPLETE_MONTH} AS m),
mon AS (SELECT generate_series(e.m - interval '2 month', e.m, interval '1 month')::date AS m FROM e),
p AS (SELECT sum("Daily_Rainfall_Total_mm") AS p FROM "6.1_core_rainfall_variables_master", e
      WHERE "District_Name" = 'City of Cape Town'
        AND "Date"::date BETWEEN e.m - interval '2 month' AND e.m + interval '1 month' - interval '1 day'),
nm AS (SELECT "Month" AS mo, max("Monthly_Total_Precipitation_mm" - "Monthly_Anomaly_mm") AS n
       FROM "6.1_core_rainfall_variables_master" WHERE "District_Name" = 'City of Cape Town' GROUP BY 1),
n AS (SELECT sum(nm.n) AS n FROM mon JOIN nm ON nm.mo = extract(month FROM mon.m))
SELECT round(((p - n) / n * 100)::numeric, 1) FROM p, n""",
    ),
    Check(
        "fixes",
        "#1 rainfall events kind=max, national, highest daily rainfall in the latest year (mm)",
        "/api/v1/rainfall/breakdown?metric=events&kind=max",
        lambda b: b["series"][0]["values"][-1],
        """SELECT round(max("Daily_Rainfall_Total_mm")::numeric, 1) FROM "6.1_core_rainfall_variables_master"
WHERE substr("Date", 1, 4) = (SELECT substr(max("Date"), 1, 4) FROM "6.1_core_rainfall_variables_master")""",
    ),
    Check(
        "fixes",
        "#2 Drought cascade, Namakwa (DC6): meteorological valueNum = SPI-6, latest complete month",
        "/api/v1/drought/table?metric=cascade&municipality=DC6",
        row("meteorological", "valueNum"),
        f"""SELECT round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric, 2)
FROM "6.2_rainfall_derived_drought_indices_master"
WHERE "District_Name" = 'Namakwa' AND make_date("Year"::int, "Month"::int, 1) = {LATEST_COMPLETE_MONTH}""",
        tol=0.005,
    ),
    Check(
        "fixes",
        "#2 Drought cascade, Namakwa (DC6): meteorological bar = class rank × 20 (None 0 … D4 100)",
        "/api/v1/drought/table?metric=cascade&municipality=DC6",
        row("meteorological", "bar"),
        f"""WITH s AS (SELECT round("SPI_6_Medium_Term_Hydrological_Impacts"::numeric, 2) AS v
           FROM "6.2_rainfall_derived_drought_indices_master"
           WHERE "District_Name" = 'Namakwa' AND make_date("Year"::int, "Month"::int, 1) = {LATEST_COMPLETE_MONTH})
SELECT CASE WHEN v <= -2.0 THEN 100 WHEN v <= -1.6 THEN 80 WHEN v <= -1.3 THEN 60
            WHEN v <= -0.8 THEN 40 WHEN v <= -0.5 THEN 20 ELSE 0 END FROM s""",
    ),
    Check(
        "fixes",
        "#2 Drought cascade, national: hydrological stage null — reservoir columns withheld (storage frozen since "
        "Dec 2021; SQL = distinct national totals since then)",
        "/api/v1/drought/table?metric=cascade",
        row("hydrological", "valueNum"),
        """SELECT count(DISTINCT round(v::numeric, 3)) FROM (SELECT date, sum(dam_storage_million_m3) AS v
FROM hydrological_drought_master WHERE date >= '2021-12-01' GROUP BY 1) s""",
        expect_null=lambda v: v == 1,
    ),
    Check(
        "categories",
        "Rainfall category: core-rainfall-variables / Seasonal_Total_JFM_mm, Alfred Nzo (DC44), 2025 — the stored column",
        "/api/v1/rainfall/category/core-rainfall-variables/seasonal-total-mm?vars=Seasonal_Total_JFM_mm&municipality=DC44",
        series_at("Seasonal_Total_JFM_mm", "2025-01-01"),
        """SELECT round(max("Seasonal_Total_JFM_mm")::numeric, 2) FROM "6.1_core_rainfall_variables_master"
WHERE "District_Name" = 'Alfred Nzo' AND "Year" = 2025""",
        tol=0.005,
    ),
    Check(
        "categories",
        "Drought category: rainfall-derived-drought-indices / RAI_Rainfall_Anomaly_Index, Cape Town, latest complete month",
        "/api/v1/drought/category/rainfall-derived-drought-indices/spi-rai-index?vars=RAI_Rainfall_Anomaly_Index"
        "&municipality=CPT",
        series_last("RAI_Rainfall_Anomaly_Index"),
        f"""SELECT round("RAI_Rainfall_Anomaly_Index"::numeric, 2) FROM "6.2_rainfall_derived_drought_indices_master"
WHERE "District_Name" = 'City of Cape Town' AND make_date("Year"::int, "Month"::int, 1) = {LATEST_COMPLETE_MONTH}""",
        tol=0.005,
    ),
    Check(
        "categories",
        "Climate category: wet-dry-spells / R10mm_Days, KwaZulu-Natal (area-weighted from 11 districts), 2025",
        "/api/v1/climate/category/wet-dry-spells/cwd-cdd-r10mm-r20mm-days?vars=R10mm_Days&province=KZN",
        series_at("R10mm_Days", "2025-01-01"),
        """SELECT "District_Name", "R10mm_Days" FROM "4_2_wet_dry_spells_master" WHERE "Year" = 2025""",
        tol=0.005,
        sql_post=lambda rows: round(_province_weighted(rows, "KZN"), 2),
    ),
    Check(
        "categories",
        "Vegetation category: satellite-remote-sensing / vhi, Cape Town, Dec 2025",
        "/api/v1/vegetation/category/satellite-remote-sensing/vci-tci-vhi-pct?vars=vhi&municipality=CPT",
        series_at("vhi", "2025-12-01"),
        """SELECT round(vhi::numeric, 1) FROM satellite_remote_sensing WHERE district_name = 'cape town'
AND date = '2025-12-01'""",
    ),
    Check(
        "categories",
        "Runoff category: runoff-variables / Soil_Moisture_Prior_m3m3, Alfred Nzo (DC44), 15 Jul 2026 (daily)",
        "/api/v1/runoff/category/runoff-variables/soil-moisture-prior-m3-m3?municipality=DC44",
        series_at("Soil_Moisture_Prior_m3m3", "2026-07-15"),
        """SELECT round("Soil_Moisture_Prior_m3m3"::numeric, 3) FROM runoff_variables_master
WHERE "District_Name" = 'Alfred Nzo' AND "Date" = '2026-07-15'""",
        tol=0.0005,
    ),
    Check(
        "audit",
        "B3 Area in severe drought or worse (D2–D4, SPI-3 ≤ −1.3), national, 2019 — mean of the monthly area shares",
        "/api/v1/climate/timeseries?metric=drought-frequency",
        lambda b: round(sum(series_at(k, "2019-01-01")(b) for k in ("d2", "d3", "d4")), 1),
        """SELECT "District_Name", "Month", "SPI_3_Short_Term_Agricultural_Drought"
FROM "6.2_rainfall_derived_drought_indices_master" WHERE "Year" = 2019""",
        tol=0.25,  # three values rounded to 0.1 each
        sql_post=_severe_share_year,
    ),
    Check(
        "audit",
        "B1 Discharge for entityId=DC44 (Alfred Nzo): monthly mean derived flow, Aug 2026 — the district, not the nation",
        "/api/v1/runoff/timeseries?metric=discharge&entityId=DC44",
        series_at("monthly", "2026-08-01"),
        """SELECT avg("Daily_Surface_Runoff_Volume_m3_day") FROM runoff_variables_master
WHERE "District_Name" = 'Alfred Nzo' AND "Date" BETWEEN '2026-08-01' AND '2026-08-31'""",
        tol=0.011,
        sql_post=_flow_m3s("DC44"),
    ),
    Check(
        "audit",
        "B4 Drought headline class, KwaZulu-Natal (rank: None 0 … D4 5) — worst class covering ≥ 20 % of the area",
        "/api/v1/drought/summary?province=KZN",
        kpi("class"),
        f"""SELECT "District_Name", "SPI_6_Medium_Term_Hydrological_Impacts" FROM "6.2_rainfall_derived_drought_indices_master"
WHERE make_date("Year"::int, "Month"::int, 1) = {LATEST_COMPLETE_MONTH}""",
        tol=0,
        sql_post=_headline_rank("KZN"),
    ),
    Check(
        "audit",
        "B4 Drought headline class, national (rank) — worst class covering ≥ 20 % of the area",
        "/api/v1/drought/summary?period=12M",
        kpi("class"),
        f"""SELECT "District_Name", "SPI_6_Medium_Term_Hydrological_Impacts" FROM "6.2_rainfall_derived_drought_indices_master"
WHERE make_date("Year"::int, "Month"::int, 1) = {LATEST_COMPLETE_MONTH}""",
        tol=0,
        sql_post=_headline_rank(None),
    ),
    # ---------------------------------------------------------------- §8 dams (DECISIONS D2)
    Check(
        "dams",
        "Total Storage KPI is null (withheld) while 8_1 still holds one storage value in 50+ districts on the newest day",
        "/api/v1/dams/summary",
        kpi("storage"),
        """SELECT count(DISTINCT district_name) FROM "8_1_reservoir_operational_master"
WHERE date = (SELECT max(date) FROM "8_1_reservoir_operational_master" WHERE date <= CURRENT_DATE)
AND storage_volume_m3 = (SELECT mode() WITHIN GROUP (ORDER BY storage_volume_m3) FROM "8_1_reservoir_operational_master")""",
        expect_null=lambda n: n is not None and int(n) >= 50,
    ),
    Check(
        "dams",
        "Reservoir card, Namakwa (DC6): catchment rainfall total for Aug 2026 (8_1 rainfall is forward-filled from Sep)",
        "/api/v1/dams/entities/DC6",
        field_value("Catchment rainfall"),
        """SELECT round(sum(rainfall_mm_day)::numeric, 0) FROM "8_1_reservoir_operational_master"
WHERE district_name = 'Namakwa' AND date BETWEEN '2026-08-01' AND '2026-08-31'""",
        tol=0.5,
    ),
    Check(
        "dams",
        "Dams category: reservoir-operational / soil_moisture_m3_m3, City of Cape Town, 15 Sep 2026 (daily)",
        "/api/v1/dams/category/reservoir-operational/soil-moisture-m3-m3?municipality=CPT",
        series_at("soil_moisture_m3_m3", "2026-09-15"),
        """SELECT round(soil_moisture_m3_m3::numeric, 3) FROM "8_1_reservoir_operational_master"
WHERE district_name = 'City of Cape Town' AND date = '2026-09-15'""",
        tol=0.0005,
    ),
    # ---------------------------------------------------------------- §9 surface water (DECISIONS D10)
    Check(
        "surface-water",
        "Mean river discharge, Vhembe (DC34): mean daily flow of the latest complete month / 86 400 s",
        "/api/v1/surface-water/summary?municipality=DC34",
        kpi("discharge"),
        """SELECT round((avg(daily_mean_flow) / 86400)::numeric, 1) FROM "9_1_hydrological_station_master"
WHERE district_name = 'Vhembe' AND date BETWEEN '2026-08-01' AND '2026-08-31'""",
    ),
    Check(
        "surface-water",
        "Mean river discharge, national: Σ over the 52 districts of the month's mean daily flow / 86 400 s",
        "/api/v1/surface-water/summary",
        kpi("discharge"),
        """SELECT round((sum(m) / 86400)::numeric, 1) FROM (SELECT district_name, avg(daily_mean_flow) m
FROM "9_1_hydrological_station_master" WHERE date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1) x""",
        tol=0.15,
    ),
    Check(
        "surface-water",
        "Suspended sediment concentration, City of Cape Town (CPT), Aug 2026 (monthly value as stored)",
        "/api/v1/surface-water/summary?municipality=CPT",
        kpi("sediment"),
        """SELECT round(max(suspended_sediment_concentration)::numeric, 0) FROM "9_1_hydrological_station_master"
WHERE district_name = 'City of Cape Town' AND year = 2026 AND month = 8""",
        tol=0.5,
    ),
    # ---------------------------------------------------------------- §10 groundwater
    Check(
        "groundwater",
        "Mean groundwater level, Vhembe (DC34): mean telemetry depth to water of the latest sampled month",
        "/api/v1/groundwater/summary?municipality=DC34",
        kpi("level"),
        """WITH m AS (SELECT max(date_trunc('month', date)) AS last FROM "10_1_borehole_and_aquifer_master"
           WHERE variable_name = 'groundwater_table_depth_m_bgl' AND date < date_trunc('month', CURRENT_DATE))
SELECT round(avg(value)::numeric, 1) FROM "10_1_borehole_and_aquifer_master", m
WHERE district_name = 'Vhembe' AND variable_name = 'groundwater_table_depth_m_bgl' AND date_trunc('month', date) = m.last""",
    ),
    Check(
        "groundwater",
        "Annual abstraction, national 2025: Σ 11.3 total abstraction over the 52 districts (Mm³/yr)",
        "/api/v1/groundwater/summary",
        kpi("abstraction"),
        """SELECT round((sum(a) / 1e6)::numeric, 1) FROM (SELECT district_name, max("Total abstraction") a
FROM "11.3_aquifer_sustainability" WHERE year = 2025 GROUP BY 1) x""",
    ),
    Check(
        "groundwater",
        "Estimated recharge, City of Cape Town (CPT): 11.2 direct recharge 2025 (mm/yr)",
        "/api/v1/groundwater/summary?municipality=CPT",
        kpi("recharge"),
        """SELECT round(max("Direct recharge")::numeric, 0) FROM "11.2_recharge_and_discharge"
WHERE district_name = 'City of Cape Town' AND year = 2025""",
        tol=0.5,
    ),
    # ---------------------------------------------------------------- §11 groundwater reserve
    Check(
        "groundwater-reserve",
        "Exploitation index, Namakwa (DC6), 2025 (11.3, %)",
        "/api/v1/groundwater-reserve/summary?municipality=DC6",
        kpi("ei"),
        """SELECT round(max("Exploitation index")::numeric, 1) FROM "11.3_aquifer_sustainability"
WHERE district_name = 'Namakwa' AND year = 2025""",
    ),
    Check(
        "groundwater-reserve",
        "Static reserve, national: Σ 11_1 static reserve volume over the 52 districts (Mm³)",
        "/api/v1/groundwater-reserve/summary",
        kpi("static"),
        """SELECT round(sum(v)::numeric, 0) FROM (SELECT district_name, max("Static reserve volume") v
FROM "11_1_hydrogeological" WHERE year = 2025 GROUP BY 1) x""",
        tol=0.5,
    ),
    Check(
        "groundwater-reserve",
        "Total recharge, Vhembe (DC34), 2025: direct recharge × district area + indirect recharge (Mm³/yr)",
        "/api/v1/groundwater-reserve/summary?municipality=DC34",
        kpi("recharge"),
        """SELECT max("Direct recharge"), max("Indirect recharge") FROM "11.2_recharge_and_discharge"
WHERE district_name = 'Vhembe' AND year = 2025""",
        tol=0.5,
        sql_post=lambda rows: round(
            float(rows[0][0]) * (get_spatial().district_areas() or {})["DC34"] / 1000 + float(rows[0][1]) / 1e6
        ),
    ),
    # ---------------------------------------------------------------- audit step 1 (docs/AUDIT_STEP1_DATA.md)
    Check(
        "audit-step1",
        "District-months in D2 or worse (SPI-3 ≤ −1.3 after rounding to 2 dp) in 2020 — the stored −1.295 (Amajuba, "
        "Aug 2020) is published as −1.30 and counts; Python / JavaScript float rounding gave −1.29",
        "/api/v1/climate/timeseries?metric=drought-frequency",
        series_at("months", "2020-01-01"),
        """SELECT count(*) FROM "6.2_rainfall_derived_drought_indices_master"
WHERE "Year" = 2020 AND round("SPI_3_Short_Term_Agricultural_Drought"::numeric, 2) <= -1.3""",
        tol=0.05,
    ),
    Check(
        "audit-step1",
        "Climate provinces table: hot days 2025, Northern Cape — area-weighted 4_1 Heatwave_Frequency_Days",
        "/api/v1/climate/table?metric=provinces",
        row("NC", "hot"),
        """SELECT "District_Name", max("Heatwave_Frequency_Days") FROM "4_1_temperature_change_master"
WHERE "Date" LIKE '2025-%' GROUP BY 1""",
        tol=0.05,
        sql_post=lambda rows: round(_province_weighted(rows, "NC"), 1),
    ),
    Check(
        "audit-step1",
        "Runoff coefficient, national, 12 months — Σ runoff / Σ rainfall of the area-weighted district totals (not the "
        "mean of the district ratios, which gave 0.186)",
        "/api/v1/runoff/summary",
        kpi("coefficient"),
        """SELECT q."District_Name", q.q, p.p FROM
(SELECT "District_Name", sum("Daily_Surface_Runoff_Volume_m3_day") / 6.5e9 * 1000 q FROM runoff_variables_master
 WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1) q
JOIN (SELECT "District_Name", sum("Daily_Rainfall_Total_mm") p FROM "6.1_core_rainfall_variables_master"
 WHERE "Date" BETWEEN '2025-09-07' AND '2026-09-06' GROUP BY 1) p USING ("District_Name")""",
        tol=0.0006,
        sql_post=lambda rows: round(_weighted_ratio(rows), 3),
    ),
    Check(
        "audit-step1",
        "Runoff discharge chart, national: the first monthly mean is the whole of September 2025 (the window starts "
        "on 7 Sep; the 7–30 Sep mean was 2 823.6)",
        "/api/v1/runoff/timeseries?metric=discharge",
        series_at("monthly", "2025-09-01"),
        """SELECT "Date", "District_Name", "Daily_Surface_Runoff_Volume_m3_day" / 6.5e9 * 1000
FROM runoff_variables_master WHERE "Date" BETWEEN '2025-09-01' AND '2025-09-30'""",
        tol=0.05,
        sql_post=lambda rows: round(_national_flow_mean(rows), 2),
    ),
    Check(
        "audit-step1",
        "Surface-water discharge chart, national: the first monthly mean is the whole of September 2025 (the window "
        "starts on 25 Sep; the 25–30 Sep mean was 991.9)",
        "/api/v1/surface-water/timeseries?metric=discharge",
        series_at("monthly", "2025-09-01"),
        """SELECT avg(q) FROM (SELECT date, sum(daily_mean_flow) / 86400 q FROM "9_1_hydrological_station_master"
WHERE date BETWEEN '2025-09-01' AND '2025-09-30' GROUP BY 1) d""",
        tol=0.05,
    ),
    Check(
        "audit-step1",
        "IWRM database view: 16_1 mean annual runoff 2025, national — the SUM of the district values (Mm³/year is an "
        "extensive unit); the category view averaged them (2 480 Mm³/year)",
        "/api/v1/iwrm/category/complete-wrm-matrix/mean-annual-runoff-mar-inter-basin-transfer-volumes-national-water-balance-return-flow-recycled-water-mm3-year?vars=Mean_Annual_Runoff_MAR_million_m3_yr",
        series_at("Mean_Annual_Runoff_MAR_million_m3_yr", "2025-01-01"),
        """SELECT sum(v) FROM (SELECT district_name, max("Mean_Annual_Runoff_MAR_million_m3_yr") v
FROM "16_1_complete_wrm_matrix_master" WHERE year = 2025 GROUP BY 1) x""",
        tol=0.6,
    ),
    Check(
        "audit-step1",
        "Surface water: daily sediment load, national, Aug 2026 — Σ over the districts of the month's mean daily load "
        "(t/day is extensive; it was area-weighted: 65.1 t/day)",
        "/api/v1/surface-water/timeseries?metric=sediment&kind=load",
        series_at("sediment", "2026-08-01"),
        """SELECT sum(v) FROM (SELECT district_name, avg(daily_sediment_load_t_day) v
FROM "9_1_hydrological_station_master" WHERE date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY 1) x""",
        tol=0.6,
    ),
    # ---------------------------------------------------------------- §12 water quality (DECISIONS D20)
    Check(
        "water-quality",
        "Nitrate relative to guideline (6 mg/L), Vhembe (DC34), latest complete month",
        "/api/v1/water-quality/map?layer=nitrate",
        lambda b: b["values"]["DC34"],
        """SELECT round(("Nitrate (NO3-N)" / 6)::numeric, 2) FROM "12.2_chemical_water_quality_master"
WHERE district_name = 'Vhembe' AND make_date(year, month, 1) =
(SELECT max(make_date(year, month, 1)) FROM "12.2_chemical_water_quality_master"
 WHERE make_date(year, month, 1) < date_trunc('month', CURRENT_DATE))""",
        tol=0.0101,  # two rounded decimals (Python and Postgres round .5 differently)
    ),
    Check(
        "water-quality",
        "EC by month, City of Cape Town (CPT): latest complete month (12.1, mS/m)",
        "/api/v1/water-quality/table?metric=latest&entityId=CPT",
        lambda b: next(r["value"] for r in b["rows"] if r["id"] == "ec"),
        """SELECT round("Electrical Conductivity (EC)"::numeric, 3) FROM "12.1_physical_water_quality_master"
WHERE district_name = 'City of Cape Town' AND make_date(year, month, 1) =
(SELECT max(make_date(year, month, 1)) FROM "12.1_physical_water_quality_master"
 WHERE make_date(year, month, 1) < date_trunc('month', CURRENT_DATE))""",
        tol=0.0005,
    ),
    Check(
        "water-quality",
        "Share of districts with nitrate within its guideline (≤ 6 mg/L), latest complete month (%)",
        "/api/v1/water-quality/breakdown?metric=param-compliance",
        lambda b: b["series"][0]["values"][b["categories"].index("Nitrate (NO₃⁻-N)")],
        """SELECT round((100.0 * avg(("Nitrate (NO3-N)" <= 6)::int))::numeric, 0)
FROM "12.2_chemical_water_quality_master" WHERE make_date(year, month, 1) =
(SELECT max(make_date(year, month, 1)) FROM "12.2_chemical_water_quality_master"
 WHERE make_date(year, month, 1) < date_trunc('month', CURRENT_DATE))""",
        tol=0.5,
    ),
]


async def run(modules: list[str]) -> list[dict[str, Any]]:
    url = dotenv_values(ROOT / ".env")["SUPABASE_DB_URL"]
    app = create_app(Settings(rate_limit_per_minute=0, log_level="WARNING"))
    results = []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://v", timeout=300) as client,
    ):
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            for ch in [c for c in CHECKS if c.module in modules]:
                r = await client.get(ch.path)
                r.raise_for_status()
                api = ch.pick(r.json())
                sql_val = ch.sql_post(conn.execute(ch.sql).fetchall())  # type: ignore[arg-type]
                if ch.expect_null is not None:
                    ok = api is None and ch.expect_null(sql_val)
                else:
                    ok = api is not None and sql_val is not None and abs(float(api) - float(sql_val)) <= ch.tol
                results.append({**ch.__dict__, "api": api, "sql_val": sql_val, "ok": ok})
                print(f"{'PASS' if ok else 'FAIL'}  {ch.what}: API {api} vs SQL {sql_val}")
    return results


def write(results: list[dict[str, Any]]) -> None:
    head = (
        "# Verification — API values vs direct SQL\n\n"
        "> Generated by `python scripts/verify.py` against the live Supabase database (read-only). Each check fetches a\n"
        "> value from the API and recomputes it with independent SQL. Area weights for province values come from\n"
        "> `reference/geo/districts.json` (the only non-SQL input).\n\n"
    )
    by_module: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_module.setdefault(r["module"], []).append(r)
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    sections = {}
    for chunk in old.split("\n## ")[1:]:
        sections[chunk.split("\n", 1)[0].split(" ")[0]] = "## " + chunk.rstrip() + "\n"
    for module, rows in by_module.items():
        lines = [f"## {module} — {dt.date.today().isoformat()}\n"]
        for r in rows:
            lines.append(f"### {'✅' if r['ok'] else '❌'} {r['what']}\n")
            lines.append(f"- API: `GET {r['path']}` → **{r['api']}**")
            lines.append(f"- SQL result: **{r['sql_val']}** (tolerance ± {r['tol']})\n")
            lines.append(f"```sql\n{r['sql'].strip()}\n```\n")
        sections[module] = "\n".join(lines)
    OUT.write_text(head + "\n".join(sections[k] for k in sorted(sections)), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    mods = sys.argv[1:] or sorted({c.module for c in CHECKS})
    res = asyncio.run(run(mods))
    write(res)
    sys.exit(0 if all(r["ok"] for r in res) else 1)
