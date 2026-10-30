"""Alerts endpoints: `GET /api/v1/alerts` (cross-module list), `/alerts/summary`, `/alerts/map?layer=locations`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import get_current_user
from app.modules.alerts.service import AlertsService
from app.modules.common import Ctx, cached, get_ctx
from app.schemas import contract as c

OPTS: dict[str, Any] = {"response_model_exclude_unset": True, "response_model_by_alias": True}
router = APIRouter(prefix="/api/v1", tags=["alerts"], dependencies=[Depends(get_current_user)])


@router.get("/alerts", response_model=c.AlertList, **OPTS)
async def alerts(
    request: Request,
    module: str | None = Query(None),
    severity: str | None = Query(None, description="critical | high | moderate | low | info"),
    province: str | None = Query(None, description="Province code: EC FS GP KZN LP MP NC NW WC"),
    ctx: Ctx = Depends(get_ctx),
) -> Any:
    ttl = ctx.state.settings.cache_ttl_summary_s
    return await cached(request, ctx, ttl, lambda: AlertsService(ctx).list(module, severity, province))


@router.get("/alerts/summary", response_model=c.KpiSet, **OPTS)
async def summary(request: Request, ctx: Ctx = Depends(get_ctx)) -> Any:
    return await cached(request, ctx, ctx.state.settings.cache_ttl_summary_s, AlertsService(ctx).summary)


@router.get("/alerts/map", response_model=c.MapLayer, **OPTS)
async def map_layer(request: Request, layer: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    return await cached(request, ctx, ctx.state.settings.cache_ttl_map_s, lambda: AlertsService(ctx).map(layer))
