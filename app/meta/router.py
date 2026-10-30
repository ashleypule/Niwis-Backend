"""/api/v1/health and /api/v1/meta/* — database connectivity, per-module status, table coverage."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends

from app import __version__
from app.auth import User, get_current_user
from app.core.provenance import iso
from app.modules import DATABASE_VIEW_MODULES, DERIVED_MODULES, IMPLEMENTED_MODULES, PARTIAL_MODULES
from app.modules.unavailable import CONTRACT_MODULES, REASONS
from app.state import AppState, get_state

router = APIRouter(prefix="/api/v1", tags=["meta"], dependencies=[Depends(get_current_user)])


@router.get("/health", summary="DB connectivity and per-module status")
async def health(state: AppState = Depends(get_state), _: User = Depends(get_current_user)) -> dict[str, Any]:
    reachable, latency, read_only = await state.db.ping()
    tables_ok: dict[str, bool] = {}
    if reachable:
        names = [ds.fq for ds in state.registry.datasets.values()]
        rows = await state.db.fetch("SELECT n, to_regclass(n) IS NOT NULL AS ok FROM unnest($1::text[]) AS n", names)
        by_fq = {r["n"]: r["ok"] for r in rows}
        tables_ok = {ds.table: bool(by_fq.get(ds.fq)) for ds in state.registry.datasets.values()}

    modules = []
    registry_modules = set(state.registry.modules)
    for module in CONTRACT_MODULES:  # every dashboard of the contract, served or not (DECISIONS D47)
        reason: str | None
        if module in DERIVED_MODULES:  # served from other modules' tables / the API's own metadata
            reads = DERIVED_MODULES[module]
            tables = sorted({ds.table for m in reads for ds in state.registry.datasets_for_module(m)})
            status = "live"
            reason = (
                f"no tables of its own: derived from {', '.join(reads)}"
                if reads
                else "no tables of its own: served from the registry and the tables' statistics"
            )
        elif module in registry_modules:
            tables = [ds.table for ds in state.registry.datasets_for_module(module)]
            missing = [t for t in tables if not tables_ok.get(t)]
            if missing:
                status, reason = "unavailable", f"tables missing: {', '.join(missing)}"
            elif module in DATABASE_VIEW_MODULES and module not in IMPLEMENTED_MODULES:
                status, reason = "partial", "only the database variables of its tables are served (/categories)"
            elif module not in IMPLEMENTED_MODULES:
                status, reason = "unavailable", "API endpoints not implemented yet"
            else:
                status, reason = "live", None
        elif module in PARTIAL_MODULES:
            tables, status, reason = [], "partial", PARTIAL_MODULES[module]
        else:  # no table in the database for this dashboard
            tables, status = [], "unavailable"
            reason = REASONS.get(module, f"No table for the {module} dashboard in the database")
        if not reachable:
            status, reason = "unavailable", "database unreachable"
        modules.append({"module": module, "status": status, "reason": reason, "tables": tables})

    overall = "ok" if reachable and read_only else ("degraded" if reachable else "down")
    return {
        "status": overall,
        "version": __version__,
        "time": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "database": {"reachable": reachable, "latencyMs": latency, "readOnlySession": read_only},
        "registry": {
            "version": state.registry.version,
            "tables": len(state.registry.datasets),
            "variables": len(state.registry.variables),
        },
        "spatial": {
            "provinces": len(state.spatial.provinces),
            "wmas": len(state.spatial.wmas),
            "districts": len(state.spatial.districts),
            "areaWeighting": state.spatial.has_districts,
            "problems": state.spatial.district_problems,
        },
        "staleAfterDays": state.settings.stale_after_days,
        "modules": modules,
    }


@router.get("/meta/sources", summary="Per-table row counts, date coverage, completeness and freshness")
async def meta_sources(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return await state.meta.sources()


@router.get("/meta/coverage", summary="Per-module coverage roll-up")
async def meta_coverage(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return await state.meta.coverage()


@router.get("/meta/variables", summary="Every variable with its canonical source, duplicates or withheld reason")
async def meta_variables(state: AppState = Depends(get_state)) -> dict[str, Any]:
    out = state.meta.variables()
    out["newestDataDate"] = iso(await state.meta.newest_date()) if state.db.configured else None
    return out
