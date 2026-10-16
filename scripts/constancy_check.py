"""Constancy check for every live column of the database view (read-only): is a column constant over time, or
identical across districts? (docs/DATA_GAPS.md §18)

For each column, at its own resolution (values the database repeats on daily rows are read once per period):
- `identical %`: share of periods in which every reporting district has the same value;
- `frozen %`: share of periods in which no district's value changed from the previous period;
- `frozen tail`: number of trailing periods, up to the column's last period, in which nothing changed;
- `twins`: groups of districts whose whole series are identical;
- `distinct`: number of distinct values.

A column is flagged when `identical %` > 50, `frozen tail` ≥ 3 periods (30 for daily columns), `frozen %` > 20 (except
binary flags and rainfall-type columns, which legitimately repeat 0), or it has twins. Prints a Markdown table.

Usage: python scripts/constancy_check.py [--all]   (--all: also withheld and duplicate columns)
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.db import ident  # noqa: E402
from app.registry.catalogue import get_catalogue  # noqa: E402
from app.registry.registry import Dataset, load_registry  # noqa: E402

ZERO_HEAVY = ("rain", "precip", "prcptot", "chirps", "tamsat", "intensity", "runoff", "frost", "heatwave", "days")


def column_sql(ds: Dataset, column: str, resolution: str) -> str:
    loc, d = ident(ds.location.column), ds.date_sql()
    col = ds.columns.get(column)
    v = ident(column)
    if col is not None and col.cast == "double":
        v = f"NULLIF({v}, '')::double precision"
    period = (
        d if resolution == "daily" else f"date_trunc('{'month' if resolution == 'monthly' else 'year'}', {d})::date"
    )
    return f"""
WITH d AS (SELECT lower({loc}) AS n, {period} AS p, max({v}) AS v FROM {ds.fq} WHERE {ds.valid_sql(column)}
           GROUP BY 1, 2),
l AS (SELECT n, p, v, lag(v) OVER (PARTITION BY n ORDER BY p) AS pv FROM d WHERE v IS NOT NULL),
per AS (SELECT p, count(*) AS k, count(DISTINCT v) AS nd, bool_and(v = pv) AS frozen, bool_or(pv IS NULL) AS first
        FROM l GROUP BY p),
twins AS (SELECT string_agg(n, ' = ' ORDER BY n) AS g FROM (SELECT n, md5(string_agg(v::text, ',' ORDER BY p)) AS h
          FROM d WHERE v IS NOT NULL GROUP BY n) s GROUP BY h HAVING count(*) > 1)
SELECT count(*) AS periods,
       round(100.0 * count(*) FILTER (WHERE nd = 1 AND k > 1) / NULLIF(count(*), 0), 1) AS identical_pct,
       round(100.0 * count(*) FILTER (WHERE frozen AND NOT first) / NULLIF(count(*) - 1, 0), 1) AS frozen_pct,
       count(*) FILTER (WHERE p > coalesce((SELECT max(p) FROM per WHERE NOT frozen OR first), '1900-01-01'))
           AS frozen_tail,
       (SELECT count(DISTINCT v) FROM d) AS distinct_values,
       (SELECT string_agg(g, '; ') FROM twins) AS twins
FROM per"""


def main() -> int:
    url = dotenv_values(ROOT / ".env")["SUPABASE_DB_URL"]
    reg = load_registry()
    every = "--all" in sys.argv
    rows = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.execute("SET statement_timeout = 600000")
        for cat in get_catalogue().categories:
            ds = reg.datasets[cat.table]
            for x in cat.columns:
                if x.kind != "numeric" or (x.status != "live" and not every):
                    continue
                r = conn.execute(column_sql(ds, x.column, x.resolution)).fetchone()  # type: ignore[arg-type]
                assert r is not None
                periods, ident_pct, frozen_pct, tail, distinct, twins = r
                zero_heavy = (distinct is not None and distinct <= 2) or any(w in x.column.lower() for w in ZERO_HEAVY)
                flags = []
                if ident_pct is not None and ident_pct > 50:
                    flags.append("identical across districts")
                if tail >= (30 if x.resolution == "daily" else 3):
                    flags.append(f"frozen for the last {tail} periods")
                if frozen_pct is not None and frozen_pct > 20 and not zero_heavy:
                    flags.append("often unchanged")
                if twins:
                    flags.append("identical district series")
                rows.append(
                    (
                        cat.table,
                        x.column,
                        x.status,
                        x.resolution,
                        periods,
                        ident_pct,
                        frozen_pct,
                        tail,
                        distinct,
                        twins or "",
                        "; ".join(flags) or "ok",
                    )
                )
                print(f"{cat.table}.{x.column}: {rows[-1][-1]}", file=sys.stderr)
    print(
        "| Table · column | Status | Res. | Periods | Identical % | Frozen % | Frozen tail | Distinct | Twins | Result |"
    )
    print("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    for t, col, st, res, n, ip, fp, tail, nd, tw, res_flag in rows:
        print(f"| `{t}` · `{col}` | {st} | {res} | {n} | {ip} | {fp} | {tail} | {nd} | {tw} | {res_flag} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
