"""Write docs/openapi.json from the app (no database needed). Run after changing routes or models;
tests/test_openapi.py fails when the committed file is out of date."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

OUT = ROOT / "docs" / "openapi.json"


def render() -> str:
    spec = create_app(make_settings(supabase_db_url=None)).openapi()
    return json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
