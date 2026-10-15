"""Every endpoint that accepts `entityId` (B1): two districts give different series, and the series does not change
when the page's scope filter changes. Used on the fixture DB (tests/test_entity.py) and on Supabase
(tests/integration/test_entity_live.py)."""

from __future__ import annotations

from typing import Any

import httpx

from app.modules.catalog import SERVICE_MODULES
from app.registry.catalogue import get_catalogue

KINDS = {"timeseries": "metric", "table": "metric", "breakdown": "metric", "map": "layer"}


def entity_endpoints() -> list[tuple[str, dict[str, str]]]:
    """(path, params) of every entity dataset: services' ENTITY_DATASETS plus one category chart per module."""
    out: list[tuple[str, dict[str, str]]] = []
    for module, svc in SERVICE_MODULES.items():
        for kind, key in sorted(getattr(svc, "ENTITY_DATASETS", set())):
            out.append((f"/api/v1/{module}/{kind}", {KINDS[kind]: key}))
    cat = get_catalogue()
    for module in SERVICE_MODULES:
        for c in cat.for_module(module):
            live = {x.column for x in c.columns if x.status == "live"}
            g = next((g for g in c.groups if live & set(g.columns)), None)
            if g is not None:
                out.append((f"/api/v1/{module}/category/{c.id}/{g.id}", {}))
                break
    return out


def payload(body: dict[str, Any]) -> Any:
    """The data part of a response (series / rows / values), without titles and provenance."""
    for k in ("series", "rows", "values"):
        if k in body:
            return body[k]
    return body


async def check(client: httpx.AsyncClient, a: str, b: str, scopes: list[dict[str, str]]) -> tuple[list[str], int]:
    """Problems found, and how many endpoints returned data (200) for both districts."""
    problems: list[str] = []
    compared = 0
    for path, params in entity_endpoints():
        ra = await client.get(path, params={**params, "entityId": a})
        rb = await client.get(path, params={**params, "entityId": b})
        where = f"{path}?{params}"
        if ra.status_code != rb.status_code:
            problems.append(f"{where}: {a} → {ra.status_code}, {b} → {rb.status_code}")
            continue
        if ra.status_code != 200:
            if ra.status_code != 501:
                problems.append(f"{where}: HTTP {ra.status_code} {ra.text[:200]}")
            continue
        compared += 1
        pa = payload(ra.json())
        if pa == payload(rb.json()):
            problems.append(f"{where}: {a} and {b} return the same data")
        for scope in scopes:
            rs = await client.get(path, params={**params, "entityId": a, **scope})
            if rs.status_code != 200 or payload(rs.json()) != pa:
                problems.append(f"{where}: {a} changes with scope {scope} (HTTP {rs.status_code})")
        if ra.json().get("aggregation") not in (None, "district"):
            problems.append(f"{where}: aggregation {ra.json().get('aggregation')!r} for one district")
    return problems, compared
