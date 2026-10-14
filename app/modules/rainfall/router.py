"""Rainfall endpoints (contract `rainfall`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import get_current_user
from app.modules.common import Ctx, cached, get_ctx, unknown
from app.modules.rainfall.service import RainfallService
from app.schemas import contract as c

router = APIRouter(prefix="/api/v1/rainfall", tags=["rainfall"], dependencies=[Depends(get_current_user)])
OPTS: dict[str, Any] = {"response_model_exclude_unset": True, "response_model_by_alias": True}
ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": c.ErrorBody, "description": "Bad query parameter"},
    404: {"model": c.ErrorBody, "description": "Unknown metric / layer / entity"},
    501: {"model": c.ErrorBody, "description": "DATA_UNAVAILABLE — the database cannot serve this yet"},
}


@router.get("/summary", response_model=c.KpiSet, responses=ERRORS, **OPTS)
async def summary(request: Request, ctx: Ctx = Depends(get_ctx)) -> Any:
    ttl = ctx.state.settings.cache_ttl_summary_s
    return await cached(request, ctx.state, ttl, RainfallService(ctx).summary)


@router.get("/timeseries", response_model=c.SeriesSet, responses=ERRORS, **OPTS)
async def timeseries(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    ttl = ctx.state.settings.cache_ttl_long_series_s
    return await cached(request, ctx.state, ttl, lambda: RainfallService(ctx).timeseries(metric))


@router.get("/forecast", response_model=c.SeriesSet, responses=ERRORS, **OPTS)
async def forecast(metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    return await RainfallService(ctx).forecast(metric)


@router.get("/breakdown", response_model=c.CategorySet, responses=ERRORS, **OPTS)
async def breakdown(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    ttl = ctx.state.settings.cache_ttl_summary_s
    return await cached(request, ctx.state, ttl, lambda: RainfallService(ctx).breakdown(metric))


@router.get("/table", response_model=c.TableSet, responses=ERRORS, **OPTS)
async def table(
    request: Request,
    metric: str = Query(...),
    limit: int | None = Query(None, ge=1, le=500),
    ctx: Ctx = Depends(get_ctx),
) -> Any:
    ttl = ctx.state.settings.cache_ttl_summary_s
    return await cached(request, ctx.state, ttl, lambda: RainfallService(ctx).table(metric, limit))


@router.get("/matrix", response_model=c.MatrixSet, responses=ERRORS, **OPTS)
async def matrix(metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    raise unknown("metric", metric, [])  # the contract defines no rainfall matrices


@router.get("/map", response_model=c.MapLayer, responses=ERRORS, **OPTS)
async def map_layer(request: Request, layer: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    ttl = ctx.state.settings.cache_ttl_map_s
    return await cached(request, ctx.state, ttl, lambda: RainfallService(ctx).map(layer))


@router.get("/entities", response_model=c.EntityList, responses=ERRORS, **OPTS)
async def entities(ctx: Ctx = Depends(get_ctx)) -> Any:
    return await RainfallService(ctx).entities()


@router.get("/entities/{code}", response_model=c.EntityDetail, responses=ERRORS, **OPTS)
async def entity(request: Request, code: str, ctx: Ctx = Depends(get_ctx)) -> Any:
    ttl = ctx.state.settings.cache_ttl_summary_s
    return await cached(request, ctx.state, ttl, lambda: RainfallService(ctx).entity(code))
