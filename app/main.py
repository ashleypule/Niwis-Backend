"""NIWIS backend API — FastAPI application. Interactive docs at /docs, schema at /openapi.json."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
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
from app.modules.rainfall.router import router as rainfall_router
from app.modules.rainfall.service import prewarm as prewarm_rainfall
from app.refdata.router import router as reference_router
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
    """Pre-load the newest DB date, the rainfall frames and the table statistics (~15 s cold) in the background."""
    try:
        await state.meta.newest_date()
        await prewarm_rainfall(state.data)
        log.info("data cache warmed")
        await state.meta.table_stats()
        log.info("meta cache warmed")
    except Exception:
        log.exception("meta cache warm-up failed")


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        registry = get_registry()  # raises on an invalid registry: refuse to start
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
        warm = asyncio.create_task(_warm(app.state.niwis)) if database.configured else None
        yield
        if warm is not None and not warm.done():
            warm.cancel()
        await database.close()

    app = FastAPI(
        title="NIWIS API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
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
    return app


app = create_app()
