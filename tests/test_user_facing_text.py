"""B6: no developer note reaches the UI. Every response in the catalog (one district scope) is scanned for wording
meant for developers: contract labels, documentation paths, decision ids."""

from __future__ import annotations

import re

import httpx
import pytest

from app.modules import catalog
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
DEV_WORDING = re.compile(r"Contract label|contract convention|docs/|DATA_GAPS|\.md\b|\.ts\b|decision D\d|\(D\d\)", re.I)


async def test_no_developer_wording_in_responses(client: httpx.AsyncClient) -> None:
    found = []
    for path, params in await catalog.requests(client, [{"municipality": "DC44"}]):
        r = await catalog.fetch(client, path, params)
        if m := DEV_WORDING.search(r.text):
            found.append(f"{catalog.describe(path, params)}: …{r.text[max(0, m.start() - 60) : m.end() + 40]}…")
    assert not found, "\n".join(found[:20])


def _null_dates(body: object, where: str = "") -> list[str]:
    """Paths of `asOf` / `periodEnd` keys sent as null (outside table rows)."""
    out: list[str] = []
    if isinstance(body, dict):
        out += [f"{where}.{k}" for k in ("asOf", "periodEnd") if k in body and body[k] is None]
        for k, v in body.items():
            if k != "rows":
                out += _null_dates(v, f"{where}.{k}")
    elif isinstance(body, list):
        for i, v in enumerate(body):
            out += _null_dates(v, f"{where}[{i}]")
    return out


async def test_no_optional_date_is_sent_as_null(client: httpx.AsyncClient) -> None:
    """BACKEND_ISSUES #10: optional fields are omitted, never null (e.g. withheld-only category groups)."""
    found = []
    for path, params in await catalog.requests(client, [{"municipality": "DC44"}, {}]):
        r = await catalog.fetch(client, path, params)
        if r.status_code == 200 and (bad := _null_dates(r.json())):
            found.append(f"{catalog.describe(path, params)}: {bad[:3]}")
    assert not found, "; ".join(found[:20])
