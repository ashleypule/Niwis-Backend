"""GET /api/v1/reference/{provinces|wmas|catchments|municipalities} — cascading filter options (contract RefList)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.core import districts
from app.core.errors import DataUnavailable, NotFound
from app.schemas.contract import RefItem, RefList
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
    elif kind == "catchments":
        raise DataUnavailable(
            "Catchment filtering is not available: NIWIS data are held per district municipality.",
            missing=["catchment-level data"],
        )
    else:
        raise NotFound(
            f"Unknown reference list {kind!r}",
            issues=[{"allowed": ["provinces", "wmas", "catchments", "municipalities"]}],
        )
    return RefList(type=kind, items=items).dump()
