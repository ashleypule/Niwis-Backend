"""migrations/proposed/001_readonly_api_role.sql grants SELECT on exactly the registered tables, and nothing else."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from app.registry.registry import get_registry

ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "migrations" / "proposed" / "001_readonly_api_role.sql"


def _gen() -> object:
    spec = importlib.util.spec_from_file_location("gen_readonly_role", ROOT / "scripts" / "gen_readonly_role.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_is_up_to_date() -> None:
    assert SQL.read_text(encoding="utf-8") == _gen().render(), "run python scripts/gen_readonly_role.py"  # type: ignore[attr-defined]


def test_select_only_on_every_registered_table() -> None:
    text = SQL.read_text(encoding="utf-8")
    granted = set(re.findall(r'^GRANT SELECT ON "public"\."([^"]+)" TO niwis_api_ro;$', text, re.M))
    assert granted == {d.table for d in get_registry().datasets.values()}
    body = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
    for word in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALL PRIVILEGES", "SUPERUSER BYPASSRLS"):
        assert not re.search(rf"GRANT[^;]*{word}", body), word
    assert "default_transaction_read_only = on" in body and "NOBYPASSRLS" in body
