"""GET /api/v1/reference/{provinces|wmas|catchments|municipalities|local-municipalities} — cascading filter options
(contract RefList) — and GET /api/v1/search (places)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth import get_current_user
from app.core import districts
from app.core.errors import DataUnavailable, NotFound
from app.refdata.places import local_municipalities, search
from app.schemas.contract import RefItem, RefList, SearchResult
from app.state import AppState, get_state

router = APIRouter(prefix="/api/v1/reference", tags=["reference"], dependencies=[Depends(get_current_user)])


@router.get("/{kind}", response_model=RefList, response_model_exclude_unset=True, response_model_by_alias=True)
async def reference(kind: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    if kind == "provinces":
        regions = state.spatial.provinces
        items = [RefItem(id=r.id, name=r.name) for r in sorted(regions, key=lambda r: r.id)] or [
            RefItem(id=k, name=v) for k, v in districts.PROVINCES.items()
        ]
    elif kind == "wmas":
        items = [RefItem(id=r.id, name=r.name) for r in sorted(state.spatial.wmas, key=lambda r: int(r.id[3:]))]
    elif kind == "municipalities":
        # District / metro municipalities: the finest level NIWIS data exist at (decision D1).
        items = [
            RefItem(id=d.code, name=d.official_name, parent=d.province, province=d.province)
            for d in sorted(districts.districts(), key=lambda d: (d.province, d.official_name))
        ]
    elif kind == "local-municipalities":
        # The 213 local municipalities with their district (`parent`); data are served per district (D1), so a local
        # municipality filter rolls up to its district.
        items = list(local_municipalities())
    elif kind == "catchments":
        raise DataUnavailable(
            "Catchment filtering is not available: NIWIS data are held per district municipality.",
            missing=["catchment-level data"],
        )
    else:
        raise NotFound(
            f"Unknown reference list {kind!r}",
            issues=[{"allowed": ["provinces", "wmas", "catchments", "municipalities", "local-municipalities"]}],
        )
    return RefList(type=kind, items=items).dump()


search_router = APIRouter(prefix="/api/v1", tags=["reference"], dependencies=[Depends(get_current_user)])


@search_router.get("/search", response_model=SearchResult, response_model_exclude_none=True)
async def search_places(q: str = Query("", max_length=100)) -> dict[str, Any]:
    """Provinces, WMAs, districts and local municipalities whose name (or code) contains `q` (≥ 2 characters)."""
    return SearchResult(q=q, hits=search(q)).dump()
