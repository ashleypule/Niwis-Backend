"""docs/openapi.json must match the app. Regenerate with `python scripts/export_openapi.py`."""

from __future__ import annotations

from scripts.export_openapi import OUT, render


def test_openapi_export_is_current() -> None:
    assert OUT.exists(), "run: python scripts/export_openapi.py"
    assert OUT.read_text(encoding="utf-8") == render(), (
        "docs/openapi.json is stale — run: python scripts/export_openapi.py"
    )
