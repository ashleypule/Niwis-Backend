"""NIWIS backend API — FastAPI application. Interactive docs at /docs, schema at /openapi.json."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import Settings, get_settings
from app.core.cache import MemoryCache
from app.core.data import DataAccess
from app.core.errors import APIError, DataUnavailable, NotFound
from app.core.filters import Filters, Window, parse_scope
from app.core.spatial import get_spatial
from app.db import Database
from app.forecasting.contract import router as forecast_contract_router
from app.forecasting.jobs import ForecastJobs
from app.forecasting.pages import PageForecasts
from app.forecasting.router import router as forecast_router
from app.logging_config import configure_logging
from app.meta.router import router as meta_router
from app.meta.service import MetaService
from app.middleware import (
    CacheControlMiddleware,
    RateLimitMiddleware,
    RequestLimitMiddleware,
    RequestLogMiddleware,
    SecurityHeadersMiddleware,
    security_headers,
)
from app.modules import DATABASE_VIEW_MODULES, IMPLEMENTED_MODULES, catalog
from app.modules.alerts.router import router as alerts_router
from app.modules.alerts.service import AlertsService
from app.modules.blue_drop.router import router as blue_drop_router
from app.modules.blue_drop.service import prewarm as prewarm_blue_drop
from app.modules.categories import build_category_router
from app.modules.categories import keep_warm as keep_categories_warm
from app.modules.climate.router import router as climate_router
from app.modules.climate.service import prewarm as prewarm_climate
from app.modules.common import Ctx
from app.modules.dams.router import router as dams_router
from app.modules.dams.service import prewarm as prewarm_dams
from app.modules.drought.router import router as drought_router
from app.modules.drought.service import prewarm as prewarm_drought
from app.modules.green_drop.router import router as green_drop_router
from app.modules.green_drop.service import prewarm as prewarm_green_drop
from app.modules.groundwater.router import router as groundwater_router
from app.modules.groundwater.service import prewarm as prewarm_groundwater
from app.modules.groundwater_reserve.router import router as groundwater_reserve_router
from app.modules.groundwater_reserve.service import prewarm as prewarm_groundwater_reserve
from app.modules.iwrm.router import router as iwrm_router
from app.modules.iwrm.service import prewarm as prewarm_iwrm
from app.modules.meta_pages.router import data_integration_router, governance_router
from app.modules.meta_pages.service import DataIntegrationService, GovernanceService
from app.modules.rainfall.router import router as rainfall_router
from app.modules.rainfall.service import prewarm as prewarm_rainfall
from app.modules.runoff.router import router as runoff_router
from app.modules.runoff.service import prewarm as prewarm_runoff
from app.modules.runoff.service import prewarm_views as prewarm_runoff_views
from app.modules.rwqo.router import router as rwqo_router
from app.modules.rwqo.service import prewarm as prewarm_rwqo
from app.modules.services.router import router as services_router
from app.modules.services.service import prewarm as prewarm_services
from app.modules.surface_water.router import router as surface_water_router
from app.modules.surface_water.service import prewarm as prewarm_surface_water
from app.modules.tariffs.router import router as tariffs_router
from app.modules.tariffs.service import prewarm as prewarm_tariffs
from app.modules.unavailable import router as unavailable_router
from app.modules.vegetation.router import router as vegetation_router
from app.modules.vegetation.service import prewarm as prewarm_vegetation
from app.modules.water_quality.router import router as water_quality_router
from app.modules.water_quality.service import prewarm as prewarm_water_quality
from app.modules.water_quantity.router import router as water_quantity_router
from app.modules.water_quantity.service import prewarm as prewarm_water_quantity
from app.refdata.router import router as reference_router
from app.refdata.router import search_router
from app.registry.catalogue import get_catalogue
from app.registry.registry import get_registry
from app.state import AppState

log = logging.getLogger("niwis")

DESCRIPTION = """
Backend for the National Integrated Water Information System (DWS). Serves the frontend contract
(`reference/API_CONTRACT.md`) from the NIWIS Supabase database, read-only.

* All data are per **district municipality** (52). See `docs/CONTRACT_CHANGES.md` for additions to the contract:
  provenance (`asOf`, `source`, `isStale`, `components`, `baseline`, `aggregation`) and the
  **501 `DATA_UNAVAILABLE`** convention for panels the database cannot serve yet.
* `/api/v1/health` and `/api/v1/meta/*` describe database coverage and freshness.
"""


async def _warm_summary(state: AppState, svc: type[Any]) -> None:
    """A page's KPIs for the default view (national, 12 months): loads every frame the page's first request needs,
    including derived ones (land cover, SGI, WQI, flow status). A 501 is a valid answer: its reads are cached too."""
    newest = await state.meta.newest_date()
    ctx = Ctx(state, Filters(parse_scope(), Window("12M", None, None), False, None), newest)
    with contextlib.suppress(DataUnavailable):
        await svc(ctx).summary()


async def _warm_entity(state: AppState, module: str, svc: type[Any]) -> None:
    """A page's entity card: its fields read frames the summary does not (one card loads them for every district)."""
    newest = await state.meta.newest_date()
    ctx = Ctx(state, Filters(parse_scope(), Window("12M", None, None), False, None), newest)
    code = catalog.SAMPLE_ENTITIES.get(module, catalog.SAMPLE_ENTITY)
    with contextlib.suppress(DataUnavailable, NotFound):
        await svc(ctx).entity(code)


def page_services() -> dict[str, type[Any]]:
    """Every live page with a summary of its own (the Overview shows other modules' panels; the AI page reads the page
    forecasts, kept fresh by their own loop)."""
    return {
        **{m: catalog.service_class(m) for m in catalog.SERVICE_MODULES},
        "alerts": AlertsService,
        "data-integration": DataIntegrationService,
        "governance": GovernanceService,
    }


async def _warm(state: AppState) -> dict[str, float | None]:
    """Pre-load the newest DB date, the module frames, each page's summary, the table statistics and the category dates
    in the background. Logs one line per step with its duration and returns step → seconds (None: failed twice).

    Each step is independent: one that fails (e.g. a statement timeout on an unindexed table) is logged and the
    others still run; a failed step is retried once at the end, when nothing else is competing for the pool, and the
    request that needs it later simply loads it then.
    """
    steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
        ("newest date", state.meta.newest_date),
        ("rainfall", lambda: prewarm_rainfall(state.data)),
        ("drought", lambda: prewarm_drought(state.data)),
        ("climate", lambda: prewarm_climate(state.data)),
        ("vegetation", lambda: prewarm_vegetation(state.data)),
        ("runoff", lambda: prewarm_runoff(state.data)),
        ("runoff seasonal and events", lambda: prewarm_runoff_views(state)),
        ("dams", lambda: prewarm_dams(state.data)),
        ("surface water", lambda: prewarm_surface_water(state.data)),
        ("groundwater", lambda: prewarm_groundwater(state.data)),
        ("groundwater reserve", lambda: prewarm_groundwater_reserve(state.data)),
        ("water quality", lambda: prewarm_water_quality(state.data)),
        ("water quantity", lambda: prewarm_water_quantity(state.data)),
        ("iwrm", lambda: prewarm_iwrm(state.data)),
        ("green drop", lambda: prewarm_green_drop(state.data)),
        ("blue drop", lambda: prewarm_blue_drop(state.data)),
        ("rwqo", lambda: prewarm_rwqo(state.data)),
        ("services", lambda: prewarm_services(state.data)),
        ("tariffs", lambda: prewarm_tariffs(state.data)),
        *((f"{page} summary", functools.partial(_warm_summary, state, svc)) for page, svc in page_services().items()),
        *(
            (f"{page} entity card", functools.partial(_warm_entity, state, page, svc))
            for page, svc in page_services().items()
            if hasattr(svc, "entity")
        ),
        ("table statistics", state.meta.table_stats),
    ]

    async def run(name: str, step: Callable[[], Awaitable[Any]]) -> float | None:
        t0 = time.perf_counter()
        try:
            await step()
        except Exception:
            log.warning("cache warm-up step failed", exc_info=True, extra={"step": name})
            return None
        seconds = round(time.perf_counter() - t0, 1)
        log.info("cache warm-up step", extra={"step": name, "seconds": seconds})
        return seconds

    t0 = time.perf_counter()
    results = {name: await run(name, step) for name, step in steps}
    for name, step in steps:
        if results[name] is None:
            results[name] = await run(name, step)
    failed = [k for k, v in results.items() if v is None]
    if failed:
        log.error("cache warm-up steps failed twice", extra={"steps": failed})
    log.info("cache warm-up finished", extra={"seconds": round(time.perf_counter() - t0, 1), "failed": failed})
    return results


async def _keep_warm(state: AppState) -> None:
    """Run the warm-up now and again every 80 % of the long-series TTL: reading an entry after 75 % of its lifetime
    reloads it in the background (app.core.cache), so the module frames never expire into a cold request."""
    while True:
        await _warm(state)
        state.warm_done.set()
        await asyncio.sleep(max(300, int(state.settings.cache_ttl_long_series_s * 0.8)))


async def _after_warm(state: AppState, job: Callable[[], Awaitable[None]]) -> None:
    await state.warm_done.wait()
    await job()


def cache_ttl(settings: Settings) -> Callable[[str], int | None]:
    """The browser / proxy cache lifetime of a path: the server cache TTL of what it serves (None = not cached)."""

    def ttl(path: str) -> int | None:
        if not path.startswith("/api/v1/") or path == "/api/v1/health":
            return None
        if path.startswith("/api/v1/forecast/"):  # runs change by the second; models / targets with the catalogue
            return settings.cache_ttl_meta_s if path.rstrip("/").endswith(("/models", "/targets")) else None
        last = path.rstrip("/").rsplit("/", 1)[-1]
        if path.startswith(("/api/v1/meta/", "/api/v1/reference/")) or last == "categories":
            return settings.cache_ttl_meta_s
        if last == "summary":
            return settings.cache_ttl_summary_s
        if last == "map":
            return settings.cache_ttl_map_s
        return settings.cache_ttl_long_series_s

    return ttl


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.check_production()  # ENV=production without DB_READONLY_ROLE_CONFIRMED=true: refuse to start
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        registry = get_registry()  # raises on an invalid registry: refuse to start
        get_catalogue()  # raises on an invalid catalogue (app/registry/catalogue.yaml)
        database = db or Database(settings)
        try:
            await database.connect()
        except Exception:
            log.exception("database connection failed at startup")
        cache = MemoryCache()
        app.state.niwis = AppState(
            settings=settings,
            db=database,
            registry=registry,
            cache=cache,
            meta=MetaService(database, registry, cache, settings),
            spatial=get_spatial(),
            data=DataAccess(database, registry, cache, settings),
        )
        state: AppState = app.state.niwis
        if settings.forecast_enabled:
            state.forecast_jobs = ForecastJobs(settings)
            state.page_forecasts = PageForecasts(state, state.forecast_jobs)
        log.info("startup complete", extra={"tables": len(registry.datasets), "env": settings.environment})
        tasks = []
        if database.configured:
            tasks.append(asyncio.create_task(_keep_warm(state)))
            # the two below wait for the first warm-up round: run beside it they compete for the pool and its
            # statements time out on a cold database (a request's pool wait is unaffected: at most two run at once)
            if settings.prewarm_categories:  # every category chart group, refreshed before the cache expires
                tasks.append(asyncio.create_task(keep_categories_warm(state)))
            if settings.forecast_enabled and settings.forecast_pages:  # page forecasts, recomputed on new data
                tasks.append(asyncio.create_task(_after_warm(state, state.page_forecasts.keep_fresh)))
        yield
        for task in tasks:
            if not task.done():
                task.cancel()
        if state.forecast_jobs is not None:
            await state.forecast_jobs.close()
        await database.close()

    app = FastAPI(
        title="NIWIS API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    # outermost last: CORS → security headers → request log → size limits → rate limit → cache headers → routes
    app.add_middleware(CacheControlMiddleware, ttl_for=cache_ttl(settings))
    app.add_middleware(
        RateLimitMiddleware,
        per_minute=settings.rate_limit_per_minute,
        trusted=settings.rate_limit_trusted_cidrs,
        trust_proxy_headers=settings.trust_proxy_headers,
        proxies=settings.trusted_proxy_cidrs,
    )
    app.add_middleware(
        RequestLimitMiddleware, max_body=settings.max_request_body_bytes, max_query=settings.max_query_length
    )
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "POST", "OPTIONS"],  # POST: /api/v1/forecast/runs only
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
        allow_credentials=False,
    )

    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        issues: list[dict[str, Any]] = [
            {"param": ".".join(str(p) for p in e.get("loc", ())[1:]), "message": e.get("msg")} for e in exc.errors()
        ]
        return JSONResponse({"error": "Invalid request parameters", "code": "BAD_REQUEST", "issues": issues}, 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        msg = "Not found" if exc.status_code == 404 else str(exc.detail)
        return JSONResponse({"error": msg}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        log.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
        # never the exception or a trace; this response bypasses the middleware stack, so it sets its own headers
        headers = {**security_headers(request.url.path, settings.is_production), "Cache-Control": "no-store"}
        if rid:
            headers["X-Request-Id"] = rid
        return JSONResponse({"error": "Internal server error", "requestId": rid}, status_code=500, headers=headers)

    app.include_router(meta_router)
    app.include_router(reference_router)
    app.include_router(search_router)
    app.include_router(rainfall_router)
    app.include_router(drought_router)
    app.include_router(climate_router)
    app.include_router(vegetation_router)
    app.include_router(runoff_router)
    app.include_router(dams_router)
    app.include_router(surface_water_router)
    app.include_router(groundwater_router)
    app.include_router(groundwater_reserve_router)
    app.include_router(water_quality_router)
    app.include_router(water_quantity_router)
    app.include_router(iwrm_router)
    app.include_router(green_drop_router)
    app.include_router(blue_drop_router)
    app.include_router(rwqo_router)
    app.include_router(services_router)
    app.include_router(tariffs_router)
    app.include_router(alerts_router)
    app.include_router(data_integration_router)
    app.include_router(governance_router)
    app.include_router(forecast_router)
    app.include_router(forecast_contract_router)  # ai / overview forecast keys that follow from the page forecasts
    for module in sorted(IMPLEMENTED_MODULES | DATABASE_VIEW_MODULES):
        app.include_router(build_category_router(module))
    app.include_router(unavailable_router)  # last: 501 with a reason for contract paths no router above serves
    return app


app = create_app()
