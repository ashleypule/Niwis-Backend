"""Run the read-only data-quality checks in scripts/data_checks.sql (or another file) and print the results.

Usage: python scripts/run_checks.py scripts/data_checks.sql
Blocks are a title line followed by SQL, separated by lines containing only ---. Never prints secrets.
"""

import sys

import psycopg
from dotenv import dotenv_values

url = dotenv_values(sys.argv[2] if len(sys.argv) > 2 else ".env")["SUPABASE_DB_URL"]
with open(sys.argv[1], encoding="utf-8") as fh:
    text = fh.read()
blocks = [b.strip() for b in text.split("\n---\n") if b.strip()]
with psycopg.connect(url, autocommit=True) as c:
    c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    c.execute("SET statement_timeout = 120000")
    for b in blocks:
        # Leading "-- ..." lines are file/block comments, not the title.
        lines = b.splitlines()
        while lines and lines[0].lstrip().startswith("--"):
            lines.pop(0)
        if len(lines) < 2:
            continue
        title, sql = lines[0], "\n".join(lines[1:])
        print(f"\n### {title}")
        try:
            cur = c.execute(sql)
            if cur.description:
                print(" | ".join(d.name for d in cur.description))
                for r in cur.fetchall():
                    print(" | ".join(str(x) for x in r))
        except Exception as e:
            print("ERROR:", type(e).__name__, str(e).splitlines()[0][:200])
