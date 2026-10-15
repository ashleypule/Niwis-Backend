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
