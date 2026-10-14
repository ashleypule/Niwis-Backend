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
        "Dam storage, national, latest month (% FSC) — Σ volume / Σ capacity, capacity = volume / %FSC × 100 per district",
        "/api/v1/drought/summary",
        kpi("storage"),
        """WITH cap AS (SELECT district_name, percentile_cont(0.5) WITHIN GROUP (ORDER BY dam_storage_million_m3 / dam_level_pct_fsc * 100) AS fsc
             FROM hydrological_drought_master WHERE dam_level_pct_fsc > 0.5 GROUP BY 1),
     cur AS (SELECT district_name, dam_storage_million_m3 AS v FROM hydrological_drought_master
             WHERE date = (SELECT max(date) FROM hydrological_drought_master))
SELECT round((sum(cur.v) / sum(cap.fsc) * 100)::numeric, 1) FROM cur JOIN cap USING (district_name)""",
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
        "Surface soil-moisture anomaly, Namakwa, latest month vs same month 2001–2025 (%)",
        "/api/v1/drought/summary?municipality=DC6",
        kpi("soilMoisture"),
        """WITH cur AS (SELECT date, surface_soil_moisture AS v FROM satellite_remote_sensing
             WHERE district_name = 'namakwa' AND date = (SELECT max(date) FROM satellite_remote_sensing)),
     clim AS (SELECT avg(surface_soil_moisture) AS n FROM satellite_remote_sensing, cur
              WHERE district_name = 'namakwa' AND extract(month FROM satellite_remote_sensing.date) = extract(month FROM cur.date)
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
    OUT.write_text(head + "\n".join(sections[k] for k in sorted(sections)), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    mods = sys.argv[1:] or sorted({c.module for c in CHECKS})
    res = asyncio.run(run(mods))
    write(res)
    sys.exit(0 if all(r["ok"] for r in res) else 1)
