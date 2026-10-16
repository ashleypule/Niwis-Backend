"""NIWIS backend API — FastAPI application. Interactive docs at /docs, schema at /openapi.json."""

from __future__ import annotations

import asyncio
import logging
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
from app.core.errors import APIError
from app.core.spatial import get_spatial
from app.db import Database
from app.logging_config import configure_logging
from app.meta.router import router as meta_router
from app.meta.service import MetaService
from app.middleware import RateLimitMiddleware, RequestLogMiddleware
from app.modules import IMPLEMENTED_MODULES
from app.modules.categories import build_category_router
from app.modules.categories import keep_warm as keep_categories_warm
from app.modules.climate.router import router as climate_router
from app.modules.climate.service import prewarm as prewarm_climate
from app.modules.drought.router import router as drought_router
from app.modules.drought.service import prewarm as prewarm_drought
from app.modules.rainfall.router import router as rainfall_router
from app.modules.rainfall.service import prewarm as prewarm_rainfall
from app.modules.runoff.router import router as runoff_router
from app.modules.runoff.service import prewarm as prewarm_runoff
from app.modules.vegetation.router import router as vegetation_router
from app.modules.vegetation.service import prewarm as prewarm_vegetation
from app.refdata.router import router as reference_router
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


async def _warm(state: AppState) -> None:
    """Pre-load the newest DB date, the module frames, the table statistics and the category dates in the background.

    Each step is independent: one that fails (e.g. a statement timeout on an unindexed table) is logged and the
    others still run; the request that needs it later simply loads it then.
    """
    steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
        ("newest date", state.meta.newest_date),
        ("rainfall", lambda: prewarm_rainfall(state.data)),
        ("drought", lambda: prewarm_drought(state.data)),
        ("climate", lambda: prewarm_climate(state.data)),
        ("vegetation", lambda: prewarm_vegetation(state.data)),
        ("runoff", lambda: prewarm_runoff(state.data)),
        ("table statistics", state.meta.table_stats),
    ]
    for name, step in steps:
        try:
            await step()
        except Exception:
            log.exception("cache warm-up step failed", extra={"step": name})
    log.info("cache warm-up finished")


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
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
        log.info("startup complete", extra={"tables": len(registry.datasets), "env": settings.environment})
        tasks = []
        if database.configured:
            tasks.append(asyncio.create_task(_warm(app.state.niwis)))
            if settings.prewarm_categories:  # every category chart group, refreshed before the cache expires
                tasks.append(asyncio.create_task(keep_categories_warm(app.state.niwis)))
        yield
        for task in tasks:
            if not task.done():
                task.cancel()
        await database.close()

    app = FastAPI(
        title="NIWIS API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(
        RateLimitMiddleware,
        per_minute=settings.rate_limit_per_minute,
        trusted=settings.rate_limit_trusted_cidrs,
        trust_proxy_headers=settings.trust_proxy_headers,
    )
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
        allow_credentials=False,
    )

    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code)

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
        return JSONResponse({"error": "Internal server error", "requestId": rid}, status_code=500)

    app.include_router(meta_router)
    app.include_router(reference_router)
    app.include_router(rainfall_router)
    app.include_router(drought_router)
    app.include_router(climate_router)
    app.include_router(vegetation_router)
    app.include_router(runoff_router)
    for module in IMPLEMENTED_MODULES:
        app.include_router(build_category_router(module))
    return app


app = create_app()
