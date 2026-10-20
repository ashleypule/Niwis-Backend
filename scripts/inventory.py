"""Phase 0 database inventory for the NIWIS backend.

Profiles every table listed in the NIWIS_SUPABASE_TABLES_MODULE_* variables and writes
docs/DB_INVENTORY.md (for people) and docs/db_inventory.json (for later phases).

The script is strictly read-only: the session is set to READ ONLY, so any write would be
rejected by Postgres. Connection details are never printed.

Usage:
    python scripts/inventory.py            # direct Postgres via SUPABASE_DB_URL (preferred)
    python scripts/inventory.py --rest     # force the Supabase REST fallback
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import itertools
import json
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

STATEMENT_TIMEOUT_MS = 120_000
# Above this many rows, per-column profiling runs on a TABLESAMPLE instead of the full table.
SAMPLE_THRESHOLD_ROWS = 2_000_000
LOW_CARDINALITY = 50

NUMERIC_TYPES = {"smallint", "integer", "bigint", "numeric", "real", "double precision"}
TIME_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}
TEXT_TYPES = {"text", "character varying", "character"}

TIME_NAME = re.compile(r"(^|_)(date|time|timestamp|datetime|day|dekad|month|year|yr|period|season)($|_)", re.I)
LOCATION_PATTERNS: dict[str, re.Pattern[str]] = {
    # Anchored patterns: measure columns such as NDVI_10day_composite or Catchment_Area_km2 must not match.
    "station_id": re.compile(r"^(station|stn|site|gauge|gauging|borehole|dam)(_?(id|code|no|number))?$", re.I),
    "station_name": re.compile(r"^(station|site|gauge|dam|location|place)_?name$|^name$|^location$", re.I),
    "lat": re.compile(r"^(lat|latitude|y|lat_dd|y_coord)$", re.I),
    "lon": re.compile(r"^(lon|long|lng|longitude|x|lon_dd|x_coord)$", re.I),
    "province": re.compile(r"^prov(ince)?(_?(name|code|id))?$", re.I),
    "catchment": re.compile(
        r"^(quaternary|tertiary|secondary|catchment|drainage_region)(_?(catchment|name|code|id))?$", re.I
    ),
    "wma": re.compile(r"^wma(_?(name|code|id))?$|^water_management_area", re.I),
    "municipality": re.compile(
        r"^(municipality|munic|district|metro|local_municipality|district_municipality)(_?(name|code|id))?$", re.I
    ),
    "geometry": re.compile(r"^(geom|geometry|wkt|shape|geojson|the_geom)$", re.I),
    "grid": re.compile(r"^(grid|cell|pixel|tile)(_?(id|code))?$", re.I),
}
NUMERIC_TIME_NAME = re.compile(
    r"^(year|yr|month|mon|day|dekad|doy|season|hydro_?year|hydrological_year|water_year)$", re.I
)
LONG_NAME = re.compile(r"^(variable|parameter|indicator|index_name|metric|element|var|param|measure)(_name)?$", re.I)
VALUE_NAME = re.compile(r"^(value|val|measurement|reading|amount|result)$", re.I)

UNIT_HINTS: list[tuple[re.Pattern[str], str]] = [  # first match wins, so explicit suffixes come first
    (re.compile(r"pct|percent", re.I), "%"),
    (re.compile(r"m3_?m3|soil_moist", re.I), "m³/m³"),
    (re.compile(r"m3_?s$|m3s$|_m3_s_|cumec", re.I), "m³/s"),
    (re.compile(r"million_m3|mm3|mcm", re.I), "Mm³"),
    (re.compile(r"m3_(day|year|yr)$", re.I), "m³ per period"),
    (re.compile(r"km2$", re.I), "km²"),
    (re.compile(r"_days?($|_)|days_", re.I), "days"),
    (re.compile(r"_mm($|_)|rain|precip|runoff_depth", re.I), "mm"),
    (re.compile(r"degc|_c$|temp|tmax|tmin|tavg|tmean|lst", re.I), "°C"),
    (re.compile(r"m3_?s|cumec|discharge|flow", re.I), "m³/s"),
    (re.compile(r"mm3|mcm|million_m3|volume", re.I), "Mm³"),
    (re.compile(r"pct|percent|_perc|anomaly_pct|vci|vhi|tci", re.I), "%"),
    (re.compile(r"kpa|vpd", re.I), "kPa"),
    (re.compile(r"spi|spei|ssi|sgi|ndvi|evi|savi|index", re.I), "— (dimensionless)"),
    (re.compile(r"days|cdd|cwd|r20|spell", re.I), "days"),
    (re.compile(r"m3_?m3|soil_moist|sm_", re.I), "m³/m³"),
]


# --------------------------------------------------------------------------- helpers


def table_list() -> dict[str, list[str]]:
    modules: dict[str, list[str]] = {}
    for key in sorted(os.environ):
        m = re.fullmatch(r"NIWIS_SUPABASE_TABLES_MODULE_(\d+)", key)
        if m:
            modules[f"Module {m.group(1)}"] = [t.strip() for t in os.environ[key].split(",") if t.strip()]
    return modules


def qident(name: str) -> str:
    """Quote an identifier for SQL (handles names starting with a digit)."""
    return '"' + name.replace('"', '""') + '"'


def jsonable(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, str) and len(v) > 200:
        return v[:200] + "…"
    return v


def scrub(msg: str) -> str:
    """Remove anything that could be a credential from an error message."""
    for var in ("SUPABASE_DB_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        val = os.environ.get(var)
        if val:
            msg = msg.replace(val, f"<{var}>")
    msg = re.sub(r"postgres(ql)?://[^\s'\"]+", "<db-url>", msg)
    msg = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<jwt>", msg)
    return msg


def classify_location(col: str) -> str | None:
    for kind, pat in LOCATION_PATTERNS.items():
        if pat.search(col):
            return kind
    return None


def unit_hint(col: str) -> str | None:
    for pat, unit in UNIT_HINTS:
        if pat.search(col):
            return unit
    return None


def resolution_from_steps(days: list[float]) -> str:
    if not days:
        return "unknown (single timestamp)"
    med = statistics.median(days)
    if med < 0.9:
        return f"sub-daily (median step {med * 24:.1f} h)"
    if med <= 1.5:
        return "daily"
    if 5 <= med <= 7.5:
        return "weekly / 8-day composite" if med > 6 else "pentad"
    if 8 <= med <= 11.5:
        return "dekadal"
    if 14 <= med <= 17:
        return "16-day composite / semi-monthly"
    if 27 <= med <= 32:
        return "monthly"
    if 85 <= med <= 95:
        return "seasonal (3-monthly)"
    if 360 <= med <= 370:
        return "annual"
    return f"irregular (median step {med:.1f} days)"


def expected_step_days(resolution: str) -> float | None:
    return {"daily": 1, "dekadal": 10, "monthly": 31, "annual": 366}.get(resolution)


# --------------------------------------------------------------------------- Postgres path


def profile_postgres(db_url: str, modules: dict[str, list[str]]) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    out: dict[str, Any] = {"method": "postgres", "tables": {}}
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row, connect_timeout=20) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        ro = conn.execute("SHOW default_transaction_read_only").fetchone()
        out["session_read_only"] = ro and next(iter(ro.values()))
        out["server_version"] = conn.execute("SHOW server_version").fetchone()["server_version"]
        out["current_user"] = conn.execute("SELECT current_user AS u").fetchone()["u"]

        # Does a read-only role already exist? (informs the proposed migration)
        out["roles"] = [
            r["rolname"]
            for r in conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' ORDER BY rolname"
            ).fetchall()
        ]

        for module, tables in modules.items():
            for table in tables:
                print(f"[{module}] {table} ...", flush=True)
                try:
                    out["tables"][table] = {"module": module, **profile_table_pg(conn, table)}
                except Exception as exc:
                    out["tables"][table] = {"module": module, "error": scrub(f"{type(exc).__name__}: {exc}")}
                    print("   ! " + out["tables"][table]["error"], flush=True)
    return out


def profile_table_pg(conn: Any, table: str) -> dict[str, Any]:
    loc = conn.execute(
        "SELECT table_schema, table_type FROM information_schema.tables WHERE table_name = %s "
        "ORDER BY (table_schema = 'public') DESC LIMIT 1",
        (table,),
    ).fetchone()
    if not loc:
        return {"error": "table not found in any schema visible to this role"}
    schema = loc["table_schema"]
    fq = f"{qident(schema)}.{qident(table)}"
    info: dict[str, Any] = {"schema": schema, "table_type": loc["table_type"]}

    cols = conn.execute(
        """
        SELECT c.column_name, c.data_type, c.udt_name, c.is_nullable, c.ordinal_position,
               col_description(format('%%I.%%I', c.table_schema, c.table_name)::regclass, c.ordinal_position) AS comment
        FROM information_schema.columns c
        WHERE c.table_schema = %s AND c.table_name = %s
        ORDER BY c.ordinal_position
        """,
        (schema, table),
    ).fetchall()
    info["table_comment"] = conn.execute(
        "SELECT obj_description(format('%%I.%%I', %s::text, %s::text)::regclass) AS d", (schema, table)
    ).fetchone()["d"]

    info["row_count"] = conn.execute(f"SELECT count(*) AS n FROM {fq}").fetchone()["n"]
    stats_row = conn.execute(
        "SELECT pg_total_relation_size(format('%%I.%%I', %s::text, %s::text)::regclass) AS bytes", (schema, table)
    ).fetchone()
    info["total_size_bytes"] = stats_row["bytes"]

    n = info["row_count"]
    src = fq
    if n > SAMPLE_THRESHOLD_ROWS:
        pct = max(0.1, min(100.0, 100.0 * SAMPLE_THRESHOLD_ROWS / n))
        src = f"{fq} TABLESAMPLE SYSTEM ({pct:.3f})"
        info["profile_note"] = f"column stats computed on a ~{pct:.2f}% block sample ({n:,} rows total)"

    columns: list[dict[str, Any]] = []
    for c in cols:
        name, dtype = c["column_name"], c["data_type"]
        q = qident(name)
        col: dict[str, Any] = {
            "name": name,
            "type": dtype if dtype != "USER-DEFINED" else c["udt_name"],
            "nullable": c["is_nullable"] == "YES",
            "comment": c["comment"],
        }
        is_geom = c["udt_name"] in ("geometry", "geography")
        if is_geom:
            agg = conn.execute(f"SELECT count(*) AS n, count({q}) AS nn FROM {src}").fetchone()
        else:
            agg = conn.execute(
                f"SELECT count(*) AS n, count({q}) AS nn, count(DISTINCT {q}) AS nd FROM {src}"
            ).fetchone()
            col["distinct"] = agg["nd"]
        col["null_pct"] = round(100.0 * (agg["n"] - agg["nn"]) / agg["n"], 2) if agg["n"] else None

        if dtype in NUMERIC_TYPES or dtype in TIME_TYPES:
            mm = conn.execute(f"SELECT min({q}) AS lo, max({q}) AS hi FROM {src}").fetchone()
            col["min"], col["max"] = jsonable(mm["lo"]), jsonable(mm["hi"])
            if dtype in NUMERIC_TYPES:
                av = conn.execute(f"SELECT avg({q}::float8) AS a FROM {src}").fetchone()
                col["mean"] = round(av["a"], 4) if av["a"] is not None else None
        if not is_geom and col.get("distinct") is not None and col["distinct"] <= LOW_CARDINALITY:
            vals = conn.execute(
                f"SELECT {q} AS v, count(*) AS n FROM {src} GROUP BY 1 ORDER BY 2 DESC LIMIT {LOW_CARDINALITY}"
            ).fetchall()
            col["values"] = [[jsonable(v["v"]), v["n"]] for v in vals]
        if dtype in TEXT_TYPES and not col.get("values"):
            ex = conn.execute(f"SELECT DISTINCT {q} AS v FROM {src} WHERE {q} IS NOT NULL LIMIT 5").fetchall()
            col["examples"] = [jsonable(v["v"]) for v in ex]
            ln = conn.execute(f"SELECT min(length({q})) AS lo, max(length({q})) AS hi FROM {src}").fetchone()
            col["text_length"] = [ln["lo"], ln["hi"]]
        if dtype in TEXT_TYPES and TIME_NAME.search(name):
            # Text columns that actually hold dates (common in CSV-loaded tables)
            parsed = conn.execute(
                f"SELECT count(*) FILTER (WHERE {q} ~ '^\\d{{4}}-\\d{{2}}(-\\d{{2}})?') AS iso, count({q}) AS nn FROM {src}"
            ).fetchone()
            col["looks_like_iso_date_pct"] = round(100.0 * parsed["iso"] / parsed["nn"], 1) if parsed["nn"] else None
        col["location_role"] = classify_location(name)
        col["unit_hint"] = unit_hint(name)
        columns.append(col)
    info["columns"] = columns

    info["primary_key"] = [
        r["column_name"]
        for r in conn.execute(
            """
            SELECT kcu.column_name FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = %s AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """,
            (schema, table),
        ).fetchall()
    ]
    info["indexes"] = [
        {"name": r["indexname"], "def": r["indexdef"]}
        for r in conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            (schema, table),
        ).fetchall()
    ]
    info["rls_enabled"] = conn.execute(
        "SELECT relrowsecurity AS r FROM pg_class WHERE oid = format('%%I.%%I', %s::text, %s::text)::regclass",
        (schema, table),
    ).fetchone()["r"]
    info["sample_rows"] = [jsonable(dict(r)) for r in conn.execute(f"SELECT * FROM {fq} LIMIT 5").fetchall()]

    analyse_structure(info)
    analyse_time_pg(conn, fq, info)
    return info


def analyse_structure(info: dict[str, Any]) -> None:
    cols = info["columns"]
    names = [c["name"] for c in cols]
    info["location_columns"] = {c["name"]: c["location_role"] for c in cols if c["location_role"]}
    roles = set(info["location_columns"].values())
    info["has_coordinates"] = {"lat", "lon"} <= roles or "geometry" in roles

    time_cols = [
        c["name"]
        for c in cols
        if c["type"] in TIME_TYPES
        or (c["type"] in NUMERIC_TYPES and NUMERIC_TIME_NAME.match(c["name"]))
        or (TIME_NAME.search(c["name"]) and (c.get("looks_like_iso_date_pct") or 0) > 50)
    ]
    info["time_columns"] = time_cols

    long_cols = [n for n in names if LONG_NAME.match(n)]
    val_cols = [n for n in names if VALUE_NAME.match(n)]
    if long_cols and val_cols:
        info["shape"] = f"long (variable: {long_cols[0]}, value: {val_cols[0]})"
    else:
        measures = [
            c["name"]
            for c in cols
            if c["type"] in NUMERIC_TYPES
            and c["name"] not in time_cols
            and not c["location_role"]
            and c["name"] != "id"
        ]
        info["shape"] = f"wide ({len(measures)} numeric measure columns)"
        info["measure_columns"] = measures


def analyse_time_pg(conn: Any, fq: str, info: dict[str, Any]) -> None:
    """Resolution, range and gaps for the primary time column."""
    cols = {c["name"]: c for c in info["columns"]}
    tc = info["time_columns"]
    if not tc:
        info["time"] = {"note": "no time column detected"}
        return

    # Build a SQL date expression from whatever the table uses.
    date_cols = [c for c in tc if cols[c]["type"] in TIME_TYPES]
    year_col = next(
        (c for c in tc if re.search(r"(^|_)(year|yr)($|_)", c, re.I) and cols[c]["type"] in NUMERIC_TYPES), None
    )
    month_col = next(
        (c for c in tc if re.search(r"(^|_)month($|_)", c, re.I) and cols[c]["type"] in NUMERIC_TYPES), None
    )
    text_date = next((c for c in tc if cols[c].get("looks_like_iso_date_pct")), None)

    # Prefer the finest-grained column: real date > ISO text date > year+month > year.
    if date_cols:
        primary, expr = date_cols[0], f"{qident(date_cols[0])}::date"
    elif text_date:
        primary = text_date
        q = qident(text_date)
        expr = f"(CASE WHEN {q} ~ '^\\d{{4}}-\\d{{2}}' THEN to_date(rpad(left({q}, 10), 10, '-01'), 'YYYY-MM-DD') END)"
    elif year_col and month_col:
        primary = f"{year_col}+{month_col}"
        expr = f"make_date({qident(year_col)}::int, {qident(month_col)}::int, 1)"
    elif year_col:
        primary, expr = year_col, f"make_date({qident(year_col)}::int, 1, 1)"
    else:
        info["time"] = {"note": f"time-like columns {tc} could not be converted to dates"}
        return

    t: dict[str, Any] = {"primary": primary, "sql_expr": expr}
    try:
        rng = conn.execute(
            f"SELECT min({expr}) AS lo, max({expr}) AS hi, count(DISTINCT {expr}) AS nd FROM {fq}"
        ).fetchone()
        t["min"], t["max"], t["distinct_dates"] = jsonable(rng["lo"]), jsonable(rng["hi"]), rng["nd"]
        dates = [
            r["d"]
            for r in conn.execute(
                f"SELECT DISTINCT {expr} AS d FROM {fq} WHERE {expr} IS NOT NULL ORDER BY 1 LIMIT 200000"
            ).fetchall()
        ]
        steps = [(b - a).days for a, b in itertools.pairwise(dates)]
        res = resolution_from_steps([float(s) for s in steps])
        t["resolution"] = res
        exp = expected_step_days(res)
        if exp:
            big = [(a, b) for a, b in itertools.pairwise(dates) if (b - a).days > exp * 1.5]
            t["gap_count"] = len(big)
            t["gaps_sample"] = [[a.isoformat(), b.isoformat()] for a, b in big[:10]]
            t["largest_gap_days"] = max(((b - a).days for a, b in big), default=0)
        if dates:
            t["covers_1981_2010_baseline"] = dates[0] <= dt.date(1981, 1, 31) and dates[-1] >= dt.date(2010, 12, 1)
            t["years_of_record"] = round((dates[-1] - dates[0]).days / 365.25, 1)

        # Per-location coverage: how many locations, and how long are their series?
        ids = [c for c, r in info["location_columns"].items() if r in ("station_id", "station_name", "grid")]
        if not ids and info.get("has_coordinates"):
            ids = [c for c, r in info["location_columns"].items() if r in ("lat", "lon")]
        if not ids:  # region-keyed tables (province / WMA / catchment / municipality series)
            ids = [
                c for c, r in info["location_columns"].items() if r in ("catchment", "municipality", "wma", "province")
            ][:1]
        if ids:
            key = ", ".join(qident(i) for i in ids)
            per = conn.execute(
                f"""SELECT count(*) AS locations,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY n) AS median_records,
                           min(lo) AS earliest, max(hi) AS latest,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY span) AS median_years
                    FROM (SELECT {key}, count(*) AS n, min({expr}) AS lo, max({expr}) AS hi,
                                 (max({expr}) - min({expr})) / 365.25 AS span
                          FROM {fq} GROUP BY {key}) s"""
            ).fetchone()
            t["per_location"] = {"key": ids, **{k: jsonable(v) for k, v in per.items()}}
            dup = conn.execute(
                f"SELECT count(*) AS n FROM (SELECT {key}, {expr} FROM {fq} GROUP BY {key}, {expr} HAVING count(*) > 1) d"
            ).fetchone()
            t["duplicate_location_date_pairs"] = dup["n"]
    except Exception as exc:
        t["error"] = scrub(f"{type(exc).__name__}: {exc}")
    info["time"] = t


# --------------------------------------------------------------------------- REST fallback


def profile_rest(modules: dict[str, list[str]]) -> dict[str, Any]:
    """Weaker fallback: PostgREST exposes column formats, counts and rows, but no indexes."""
    import httpx

    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_ANON_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    out: dict[str, Any] = {"method": "rest", "tables": {}}
    with httpx.Client(headers=headers, timeout=60) as client:
        spec = client.get(url + "/").json()
        defs = spec.get("definitions", {})
        for module, tables in modules.items():
            for table in tables:
                print(f"[{module}] {table} (REST) ...", flush=True)
                try:
                    d = defs.get(table)
                    if d is None:
                        out["tables"][table] = {"module": module, "error": "not exposed via PostgREST"}
                        continue
                    r = client.get(
                        f"{url}/{table}", params={"select": "*", "limit": 5}, headers={"Prefer": "count=exact"}
                    )
                    r.raise_for_status()
                    total = int(r.headers.get("content-range", "*/0").split("/")[-1])
                    columns = []
                    for name, p in d.get("properties", {}).items():
                        col = {
                            "name": name,
                            "type": p.get("format", p.get("type")),
                            "nullable": name not in d.get("required", []),
                            "comment": p.get("description"),
                            "location_role": classify_location(name),
                            "unit_hint": unit_hint(name),
                        }
                        if p.get("format") in NUMERIC_TYPES | TIME_TYPES:
                            for agg, order in (("min", "asc"), ("max", "desc")):
                                rr = client.get(
                                    f"{url}/{table}",
                                    params={"select": name, "order": f"{name}.{order}.nullslast", "limit": 1},
                                )
                                rows = rr.json() if rr.status_code == 200 else []
                                col[agg] = rows[0][name] if rows else None
                        columns.append(col)
                    info = {
                        "module": module,
                        "row_count": total,
                        "columns": columns,
                        "sample_rows": r.json(),
                        "primary_key": [],
                        "indexes": "unavailable via REST",
                    }
                    analyse_structure(info)
                    info["time"] = {"note": "resolution and gaps need direct Postgres access"}
                    out["tables"][table] = info
                except Exception as exc:
                    out["tables"][table] = {"module": module, "error": scrub(f"{type(exc).__name__}: {exc}")}
    return out


# --------------------------------------------------------------------------- report


def fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.4g}"
    if isinstance(v, int):
        return f"{v:,}"
    s = str(v).replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= 60 else s[:57] + "…"


def write_markdown(inv: dict[str, Any], path: Path) -> None:
    L: list[str] = []
    L.append("# Database inventory\n")
    L.append(
        f"> Generated by `scripts/inventory.py` on {inv['generated_at']} (read-only, method: **{inv['method']}**)."
    )
    L.append("> Re-run the script to refresh; do not edit by hand.\n")
    if inv["method"] == "postgres":
        L.append(
            f"- Server: PostgreSQL {inv.get('server_version')}; connected as `{inv.get('current_user')}`; "
            f"session read-only: `{inv.get('session_read_only')}`"
        )
        ro_roles = [r for r in inv.get("roles", []) if re.search(r"read|ro$|_ro_|viewer|niwis", r, re.I)]
        L.append(f"- Candidate read-only roles found: {', '.join(f'`{r}`' for r in ro_roles) or 'none'}\n")
    L += table_lines(inv["tables"])
    path.write_text("\n".join(L), encoding="utf-8", newline="\n")


def table_lines(tables: dict[str, Any]) -> list[str]:
    """Overview table and one section per table (shared by the full inventory and `--append`)."""
    L: list[str] = []
    L.append("## Overview\n")
    L.append("| Module | Table | Rows | Time column | Resolution | Range | Locations | Coords | Shape |")
    L.append("| --- | --- | ---: | --- | --- | --- | ---: | --- | --- |")
    for name, t in tables.items():
        if "error" in t:
            L.append(f"| {t['module']} | `{name}` | — | — | — | **error:** {fmt(t['error'])} | — | — | — |")
            continue
        tm = t.get("time", {})
        per = tm.get("per_location", {})
        L.append(
            f"| {t['module']} | `{name}` | {fmt(t['row_count'])} | {fmt(tm.get('primary'))} | {fmt(tm.get('resolution'))} | "
            f"{fmt(tm.get('min'))} → {fmt(tm.get('max'))} | {fmt(per.get('locations'))} | "
            f"{'yes' if t.get('has_coordinates') else 'no'} | {fmt(t.get('shape'))} |"
        )
    L.append("")

    for name, t in tables.items():
        L.append(f"## `{name}` ({t['module']})\n")
        if "error" in t:
            L.append(f"**Could not profile:** {t['error']}\n")
            continue
        if t.get("table_comment"):
            L.append(f"_{t['table_comment']}_\n")
        L.append(
            f"- Rows: **{t['row_count']:,}**"
            + (f" · size {t['total_size_bytes'] / 1e6:,.1f} MB" if t.get("total_size_bytes") else "")
        )
        if t.get("profile_note"):
            L.append(f"- Note: {t['profile_note']}")
        L.append(f"- Shape: {t.get('shape')}")
        L.append(f"- Primary key: {', '.join(f'`{c}`' for c in t.get('primary_key') or []) or '**none**'}")
        if isinstance(t.get("indexes"), list):
            L.append("- Indexes: " + ("; ".join(f"`{i['def']}`" for i in t["indexes"]) or "**none**"))
        if "rls_enabled" in t:
            L.append(f"- Row-level security enabled: {t['rls_enabled']}")
        L.append(
            f"- Location columns: {', '.join(f'`{k}` ({v})' for k, v in t.get('location_columns', {}).items()) or 'none detected'}; "
            f"coordinates: {'yes' if t.get('has_coordinates') else '**no**'}"
        )
        tm = t.get("time", {})
        if tm.get("primary"):
            L.append(
                f"- Time: `{tm['primary']}` · {tm.get('resolution')} · {tm.get('min')} → {tm.get('max')} · "
                f"{fmt(tm.get('distinct_dates'))} distinct dates · {tm.get('years_of_record')} years"
            )
            if "gap_count" in tm:
                L.append(
                    f"- Gaps (> 1.5 × step): {tm['gap_count']}"
                    + (
                        f", largest {tm['largest_gap_days']} days; e.g. "
                        + ", ".join(f"{a}→{b}" for a, b in tm["gaps_sample"][:5])
                        if tm["gap_count"]
                        else ""
                    )
                )
            L.append(f"- Covers 1981–2010 baseline: **{tm.get('covers_1981_2010_baseline')}**")
            if tm.get("per_location"):
                p = tm["per_location"]
                L.append(
                    f"- Per location ({', '.join(p['key'])}): {fmt(p['locations'])} locations · median "
                    f"{fmt(p['median_records'])} records · median span {fmt(p['median_years'])} years"
                )
            if tm.get("duplicate_location_date_pairs"):
                L.append(f"- ⚠ Duplicate (location, date) pairs: {tm['duplicate_location_date_pairs']:,}")
            if tm.get("error"):
                L.append(f"- Time analysis error: {tm['error']}")
        else:
            L.append(f"- Time: {tm.get('note')}")
        L.append("")
        L.append("| Column | Type | Null % | Distinct | Min | Max | Mean | Unit hint | Values / examples |")
        L.append("| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |")
        for c in t["columns"]:
            vals = c.get("values")
            ex = (
                ", ".join(f"{fmt(v)} ({n:,})" for v, n in vals[:8])
                if vals
                else ", ".join(fmt(e) for e in c.get("examples", [])[:4])
            )
            L.append(
                f"| `{c['name']}`{' 🗺' if c.get('location_role') else ''} | {c['type']}{'' if c.get('nullable') else ' NOT NULL'} | "
                f"{fmt(c.get('null_pct'))} | {fmt(c.get('distinct'))} | {fmt(c.get('min'))} | {fmt(c.get('max'))} | "
                f"{fmt(c.get('mean'))} | {fmt(c.get('unit_hint'))} | {ex} |"
            )
        if any(c.get("comment") for c in t["columns"]):
            L.append("\nColumn comments:\n")
            for c in t["columns"]:
                if c.get("comment"):
                    L.append(f"- `{c['name']}`: {c['comment']}")
        L.append("\n<details><summary>5 sample rows</summary>\n\n```json")
        L.append(json.dumps(t.get("sample_rows", []), indent=1, ensure_ascii=False, default=str))
        L.append("```\n</details>\n")
    return L


def append(db_url: str, modules: dict[str, list[str]]) -> int:
    """Profile the listed tables that docs/db_inventory.json does not have yet and append them to both files."""
    old = json.loads((DOCS / "db_inventory.json").read_text(encoding="utf-8"))
    new_mods = {m: [t for t in ts if t not in old["tables"]] for m, ts in modules.items()}
    new_mods = {m: ts for m, ts in new_mods.items() if ts}
    if not new_mods:
        print("Nothing to append: every listed table is in docs/db_inventory.json.")
        return 0
    added = profile_postgres(db_url, new_mods)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    old["tables"].update(added["tables"])
    old.setdefault("appended", []).append({"at": stamp, "tables": list(added["tables"])})
    (DOCS / "db_inventory.json").write_text(
        json.dumps(jsonable(old), indent=1, ensure_ascii=False, default=str), encoding="utf-8", newline="\n"
    )
    md = (DOCS / "DB_INVENTORY.md").read_text(encoding="utf-8").rstrip("\n")
    head = [
        "",
        "",
        f"# Tables added after Phase 0 (profiled {stamp} by `scripts/inventory.py --append`)",
        "",
        "> Same method as above, read-only. Long-format tables (one row per variable) are profiled as stored; their",
        "> variables are listed in docs/CATEGORY_CATALOGUE.md. Plausibility results: docs/DATA_GAPS.md §21.",
        "",
        "",
    ]
    lines = table_lines(added["tables"])
    lines[0] = "## Overview of the added tables\n"
    (DOCS / "DB_INVENTORY.md").write_text(md + "\n".join(head) + "\n".join(lines), encoding="utf-8", newline="\n")
    print(f"Appended {len(added['tables'])} tables to docs/DB_INVENTORY.md and docs/db_inventory.json.")
    return 0


# --------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rest", action="store_true", help="use the Supabase REST API instead of Postgres")
    ap.add_argument(
        "--append",
        action="store_true",
        help="profile only tables missing from docs/db_inventory.json and append them (keeps the Phase 0 snapshot)",
    )
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    modules = table_list()
    if not modules:
        print("No NIWIS_SUPABASE_TABLES_MODULE_* variables found. Create .env from .env.example.", file=sys.stderr)
        return 2

    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if args.append:
        return append(db_url, modules)
    try:
        if db_url and not args.rest:
            inv = profile_postgres(db_url, modules)
        else:
            print("SUPABASE_DB_URL not set (or --rest given): using the REST fallback.", flush=True)
            inv = profile_rest(modules)
    except Exception as exc:
        print("Inventory failed: " + scrub(f"{type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1

    inv["generated_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    inv["modules"] = modules
    DOCS.mkdir(exist_ok=True)
    (DOCS / "db_inventory.json").write_text(
        json.dumps(jsonable(inv), indent=1, ensure_ascii=False, default=str), encoding="utf-8", newline="\n"
    )
    write_markdown(inv, DOCS / "DB_INVENTORY.md")
    errors = [t for t, v in inv["tables"].items() if "error" in v]
    print(f"Wrote docs/DB_INVENTORY.md and docs/db_inventory.json ({len(inv['tables'])} tables, {len(errors)} errors).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
