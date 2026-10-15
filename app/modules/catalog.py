"""Every contract request the backend serves: module × endpoint × key × selector value.

Used by the response audit (tests/test_response_audit.py, tests/integration, scripts/audit_responses.py) and the
selector tests. Keys are discovered from the API itself (an unknown key → 404 listing the valid ones), and selector
values from each service module's `SELECTORS`, so a new metric or selector is audited without editing this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import product
from types import ModuleType
from typing import Any

import httpx

from app.core.response_checks import Coverage, all_violations, invariant_violations
from app.modules.climate import service as climate
from app.modules.common import Selector
from app.modules.drought import service as drought
from app.modules.rainfall import service as rainfall
from app.modules.runoff import service as runoff
from app.modules.vegetation import service as vegetation

SERVICE_MODULES: dict[str, ModuleType] = {
    "rainfall": rainfall,
    "drought": drought,
    "climate": climate,
    "vegetation": vegetation,
    "runoff": runoff,
}
ENTITY_MODULES = {"rainfall", "runoff"}
KINDS = {"timeseries": "metric", "forecast": "metric", "breakdown": "metric", "table": "metric", "matrix": "metric"}
KINDS["map"] = "layer"
PROBE = "__catalog_probe__"
SAMPLE_ENTITY = "DC44"  # a district present in the recorded fixture DB and in Supabase


def selectors(module: str) -> dict[tuple[str, str], tuple[Selector, ...]]:
    """(endpoint kind, key) → selectors declared by the module's service."""
    out: dict[tuple[str, str], tuple[Selector, ...]] = getattr(SERVICE_MODULES[module], "SELECTORS", {})
    return out


def selector_variants(sels: tuple[Selector, ...]) -> Iterator[dict[str, str]]:
    """No selector (the default), then every value of every selector (multi selectors: each value alone)."""
    yield {}
    for s in sels:
        for v in s.values:
            yield {s.name: v}


async def keys(client: httpx.AsyncClient, module: str, kind: str) -> list[str]:
    param = KINDS[kind]
    r = await client.get(f"/api/v1/{module}/{kind}", params={param: PROBE})
    assert r.status_code == 404, f"{module}/{kind}: probe returned {r.status_code}"
    allowed: list[str] = r.json()["issues"][0]["allowed"]
    return allowed


async def requests(client: httpx.AsyncClient, scopes: list[dict[str, str]]) -> list[tuple[str, dict[str, str]]]:
    """(path, params) for every summary, endpoint key and selector value, in every scope."""
    out: list[tuple[str, dict[str, str]]] = []
    for module in SERVICE_MODULES:
        base = f"/api/v1/{module}"
        for scope in scopes:
            out.append((f"{base}/summary", dict(scope)))
        if module in ENTITY_MODULES:
            out += [(f"{base}/entities", {}), (f"{base}/entities/{SAMPLE_ENTITY}", {})]
        sels = selectors(module)
        for kind, param in KINDS.items():
            for key in await keys(client, module, kind):
                for variant, scope in product(list(selector_variants(sels.get((kind, key), ()))), scopes):
                    out.append((f"{base}/{kind}", {param: key, **variant, **scope}))
    return out


def describe(path: str, params: dict[str, Any]) -> str:
    q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"{path.removeprefix('/api/v1/')}{'?' + q if q else ''}"


async def fetch(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> httpx.Response:
    """GET, retrying once with the sample `entityId` when the endpoint says it needs one."""
    r = await client.get(path, params=params)
    if r.status_code == 400 and any(i.get("param") == "entityId" for i in r.json().get("issues") or []):
        r = await client.get(path, params={**params, "entityId": SAMPLE_ENTITY})
    return r


async def audit(
    client: httpx.AsyncClient, scopes: list[dict[str, str]], coverage: Coverage | None, *, completeness: bool = True
) -> tuple[int, list[str]]:
    """Request everything in the catalog; return (requests made, problems). A problem is a status other than 200 /
    501 or a response that fails app.core.response_checks (`completeness=False`: invariants only)."""
    check = all_violations if completeness else invariant_violations
    problems: list[str] = []
    reqs = await requests(client, scopes)
    for path, params in reqs:
        r = await fetch(client, path, params)
        where = describe(path, params)
        if r.status_code not in (200, 501):
            problems.append(f"{where}: HTTP {r.status_code} {r.text[:200]}")
            continue
        if r.status_code == 200:
            problems += [f"{where}: {v}" for v in check(r.json(), coverage)]
    return len(reqs), problems
