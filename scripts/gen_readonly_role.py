"""Write migrations/proposed/001_readonly_api_role.sql from the dataset registry (every table the API reads).

    python scripts/gen_readonly_role.py            # rewrite the file
    python scripts/gen_readonly_role.py --check    # exit 1 if the file is out of date (CI / tests)

The migration is never run by the backend: the data team runs it (DEPLOYMENT.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import qualified  # noqa: E402
from app.registry.registry import get_registry  # noqa: E402

OUT = ROOT / "migrations" / "proposed" / "001_readonly_api_role.sql"

HEADER = """\
-- PROPOSED — run by the NIWIS data team (a database owner), never by the API. Generated from the dataset registry by
-- scripts/gen_readonly_role.py: regenerate it whenever a table is registered.
--
-- Creates `niwis_api_ro`, a login that can only SELECT the {n} tables the API reads. The API then connects with it
-- instead of `postgres` (the API also forces every session READ ONLY; this role makes that true at the database
-- level, and limits the damage if the API's credentials leak).
--
-- Run (psql ≥ 10; the password never appears in the file or the shell history if you type it at the prompt):
--
--   psql "<owner connection string>" -v ON_ERROR_STOP=1 \\
--        -v ro_password="$(read -rsp 'niwis_api_ro password: ' p; echo "$p")" \\
--        -f migrations/proposed/001_readonly_api_role.sql
--
-- It is idempotent (re-running updates the password and re-applies the grants) and runs in one transaction.
-- Afterwards: SUPABASE_DB_URL = the session-pooler string for user `niwis_api_ro.<project-ref>`, then
-- DB_READONLY_ROLE_CONFIRMED=true (ENV=production refuses to start without it). See DEPLOYMENT.md.
--
-- Row-level security: every public table has RLS enabled and no policies, so a normal role sees no rows. This script
-- adds one SELECT-only policy per table for this role (USING (true): the data are public). The alternative is
-- `ALTER ROLE niwis_api_ro BYPASSRLS` instead of the policies — simpler, but it bypasses any policy added later.

\\set ON_ERROR_STOP on
BEGIN;

SELECT format('CREATE ROLE niwis_api_ro LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'niwis_api_ro') \\gexec
SELECT format('ALTER ROLE niwis_api_ro LOGIN PASSWORD %L', :'ro_password') \\gexec

ALTER ROLE niwis_api_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 20;
ALTER ROLE niwis_api_ro SET default_transaction_read_only = on;
ALTER ROLE niwis_api_ro SET statement_timeout = '60s';  -- the API sets 10 s per session; background statistics 60 s
ALTER ROLE niwis_api_ro SET idle_in_transaction_session_timeout = '60s';

REVOKE ALL ON SCHEMA public FROM niwis_api_ro;
GRANT USAGE ON SCHEMA public TO niwis_api_ro;
"""

FOOTER = """
-- The meta endpoints read table sizes and index lists from the system catalogue (pg_class, pg_indexes), which every
-- role can read: no further grants. Nothing else is granted: no INSERT / UPDATE / DELETE / TRUNCATE, no sequences,
-- no functions, and no default privileges (a new table needs a new registry entry and a regenerated script).

COMMIT;

-- Check (as niwis_api_ro):  SELECT current_setting('default_transaction_read_only');  -- on
--                            CREATE TEMP TABLE t (x int);                              -- must fail (read-only)
"""


def render() -> str:
    reg = get_registry()
    tables = sorted({(d.schema_, d.table) for d in reg.datasets.values()})
    lines = [HEADER.format(n=len(tables))]
    for schema, table in tables:
        q = qualified(schema, table)
        lines.append(f"GRANT SELECT ON {q} TO niwis_api_ro;")
        lines.append(f"DROP POLICY IF EXISTS niwis_api_ro_read ON {q};")
        lines.append(f"CREATE POLICY niwis_api_ro_read ON {q} FOR SELECT TO niwis_api_ro USING (true);")
    lines.append(FOOTER)
    return "\n".join(lines)


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python scripts/gen_readonly_role.py")
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
