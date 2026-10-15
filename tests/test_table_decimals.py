"""B2: every numeric table column says how many decimals to show (the frontend rounds to `decimals ?? 0`, which turned
the runoff coefficient 0.198 into "0"). Tables whose rows hold different units also give each row its own `decimals`."""

from __future__ import annotations

import httpx
import pytest

from app.modules import catalog
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
NUMERIC = {"number", "delta", "bar", "trend"}


async def test_every_numeric_column_has_decimals(client: httpx.AsyncClient) -> None:
    missing = []
    for module in catalog.SERVICE_MODULES:
        for key in await catalog.keys(client, module, "table"):
            r = await catalog.fetch(client, f"/api/v1/{module}/table", {"metric": key, "municipality": "DC44"})
            if r.status_code != 200:
                continue
            body = r.json()
            missing += [
                f"{module}/{key}.{col['key']}"
                for col in body["columns"]
                if col["type"] in NUMERIC and col.get("decimals") is None
            ]
            for row in body["rows"]:  # per-row decimals, where given, are small non-negative integers
                for k in ("decimals", "trendDecimals"):
                    if k in row:
                        v = row[k]  # table cells are numbers (JSON 3.0 == 3)
                        assert float(v).is_integer() and 0 <= v <= 4, (module, key, row)
    assert not missing, missing


async def test_runoff_coefficient_keeps_its_decimals(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/runoff/table", params={"metric": "statistics", "municipality": "DC44"})).json()
    cols = {c["key"]: c for c in body["columns"]}
    coef = next(r for r in body["rows"] if r["id"] == "coefficient")
    assert coef["unit"] == "—" and coef["decimals"] == 3 and 0 < coef["current"] < 1
    assert cols["current"]["decimals"] >= coef["decimals"]  # the column never shows fewer decimals than a row needs
    assert cols["trend"]["decimals"] >= coef["trendDecimals"]
