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
