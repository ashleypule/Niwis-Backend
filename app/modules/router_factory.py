"""Builds the contract endpoints for a module from its service class.

Every module exposes the same contract surface (`summary`, `timeseries`, `forecast`, `breakdown`, `table`, `matrix`,
`map`, and optionally `entities`); the service decides which keys exist, which are unavailable (501) and which are
unknown (404). Responses are cached per path + query with the configured TTLs.
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, Depends, Query, Request

from app.auth import get_current_user
from app.modules.common import Ctx, cached, check_response, get_ctx, unknown
from app.schemas import contract as c

OPTS: dict[str, Any] = {"response_model_exclude_unset": True, "response_model_by_alias": True}
ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": c.ErrorBody, "description": "Bad query parameter"},
    404: {"model": c.ErrorBody, "description": "Unknown metric / layer / entity"},
    501: {"model": c.ErrorBody, "description": "DATA_UNAVAILABLE — the database cannot serve this yet"},
}


class Service(Protocol):
    def __init__(self, ctx: Ctx) -> None: ...

    async def summary(self) -> dict[str, Any]: ...

    async def timeseries(self, metric: str) -> dict[str, Any]: ...

    async def forecast(self, metric: str) -> dict[str, Any]: ...

    async def breakdown(self, metric: str) -> dict[str, Any]: ...

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]: ...

    async def map(self, layer: str) -> dict[str, Any]: ...


def build_router(module: str, svc: type[Any], *, entities: bool = False) -> APIRouter:
    router = APIRouter(prefix=f"/api/v1/{module}", tags=[module], dependencies=[Depends(get_current_user)])

    @router.get("/summary", response_model=c.KpiSet, responses=ERRORS, **OPTS)
    async def summary(request: Request, ctx: Ctx = Depends(get_ctx)) -> Any:
        return await cached(request, ctx, ctx.state.settings.cache_ttl_summary_s, svc(ctx).summary)

    @router.get("/timeseries", response_model=c.SeriesSet, responses=ERRORS, **OPTS)
    async def timeseries(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
        ttl = ctx.state.settings.cache_ttl_long_series_s
        return await cached(request, ctx, ttl, lambda: svc(ctx).timeseries(metric))

    @router.get("/forecast", response_model=c.SeriesSet, responses=ERRORS, **OPTS)
    async def forecast(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
        return await check_response(ctx, request.url.path, await svc(ctx).forecast(metric))

    @router.get("/breakdown", response_model=c.CategorySet, responses=ERRORS, **OPTS)
    async def breakdown(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
        ttl = ctx.state.settings.cache_ttl_summary_s
        return await cached(request, ctx, ttl, lambda: svc(ctx).breakdown(metric))

    @router.get("/table", response_model=c.TableSet, responses=ERRORS, **OPTS)
    async def table(
        request: Request,
        metric: str = Query(...),
        limit: int | None = Query(None, ge=1, le=500),
        ctx: Ctx = Depends(get_ctx),
    ) -> Any:
        ttl = ctx.state.settings.cache_ttl_summary_s
        return await cached(request, ctx, ttl, lambda: svc(ctx).table(metric, limit))

    @router.get("/matrix", response_model=c.MatrixSet, responses=ERRORS, **OPTS)
    async def matrix(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
        service = svc(ctx)
        if not hasattr(service, "matrix"):
            raise unknown("metric", metric, [])  # this module defines no matrices in the contract
        ttl = ctx.state.settings.cache_ttl_summary_s
        return await cached(request, ctx, ttl, lambda: service.matrix(metric))

    @router.get("/map", response_model=c.MapLayer, responses=ERRORS, **OPTS)
    async def map_layer(request: Request, layer: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
        ttl = ctx.state.settings.cache_ttl_map_s
        return await cached(request, ctx, ttl, lambda: svc(ctx).map(layer))

    if entities:

        @router.get("/entities", response_model=c.EntityList, responses=ERRORS, **OPTS)
        async def entity_list(request: Request, ctx: Ctx = Depends(get_ctx)) -> Any:
            return await check_response(ctx, request.url.path, await svc(ctx).entities())

        @router.get("/entities/{code}", response_model=c.EntityDetail, responses=ERRORS, **OPTS)
        async def entity(request: Request, code: str, ctx: Ctx = Depends(get_ctx)) -> Any:
            ttl = ctx.state.settings.cache_ttl_summary_s
            return await cached(request, ctx, ttl, lambda: svc(ctx).entity(code))

    return router
