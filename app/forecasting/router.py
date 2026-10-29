"""Forecasting Studio API.

GET  /api/v1/forecast/models              the model registry (name, group, description, minimum history, inputs,
                                          experimental flag) and the run limits
GET  /api/v1/forecast/targets?module=…    live numeric catalogue columns that can be forecast
POST /api/v1/forecast/runs                {table, column, scope, model, horizon, inputs[], resolution} → 202 {runId}
GET  /api/v1/forecast/runs/{runId}        status and progress, then the result
GET  /api/v1/forecast/pages               status of the precomputed page forecasts
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse

from app.auth import get_current_user
from app.core.errors import NotFound, ServiceUnavailable
from app.forecasting.jobs import ForecastJobs
from app.forecasting.pages import VARS, PageForecasts
from app.forecasting.service import RunIn, models_listing, submit, targets_listing
from app.modules.common import Ctx, get_ctx
from app.schemas import contract as c
from app.state import AppState, get_state

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"], dependencies=[Depends(get_current_user)])
ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": c.ErrorBody, "description": "Unknown table, column, model or scope; horizon out of range"},
    404: {"model": c.ErrorBody, "description": "Unknown or expired run"},
    422: {"model": c.ErrorBody, "description": "The series cannot support this forecast (too short, constant, gaps)"},
    429: {"model": c.ErrorBody, "description": "Too many new runs from this address (Retry-After)"},
    503: {"model": c.ErrorBody, "description": "The run queue is full (Retry-After)"},
}


def jobs_of(state: AppState) -> ForecastJobs:
    jobs: ForecastJobs | None = getattr(state, "forecast_jobs", None)
    if jobs is None:
        raise ServiceUnavailable("Forecasting is not enabled on this server")
    return jobs


@router.get("/models", summary="Forecasting models, grouped, with their requirements")
async def models(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return models_listing(state.settings)


@router.get("/targets", responses=ERRORS, summary="Catalogue columns that can be forecast")
async def targets(
    request: Request,
    module: str | None = Query(None, description="Module page, e.g. rainfall; all modules when absent"),
    ctx: Ctx = Depends(get_ctx),
) -> Any:
    key = f"fc:targets:{module}:{ctx.newest_db}"
    ttl = ctx.state.settings.cache_ttl_meta_s
    return await ctx.state.cache.get_or_set(key, ttl, lambda: targets_listing(ctx, module))


@router.post("/runs", status_code=202, responses=ERRORS, summary="Start a forecast run (trains in the background)")
async def start_run(
    request: Request,
    body: RunIn,
    state: AppState = Depends(get_state),
) -> JSONResponse:
    jobs = jobs_of(state)
    ip = getattr(request.state, "client_ip", None) or (request.client.host if request.client else "unknown")
    rec, cached = await submit(state, jobs, body, ip)
    out = {
        "runId": rec.id,
        "status": rec.status,
        "cached": cached,
        "statusUrl": f"/api/v1/forecast/runs/{rec.id}",
    }
    url = f"/api/v1/forecast/runs/{rec.id}"
    return JSONResponse(out, status_code=202, headers={"Location": url, "Cache-Control": "no-store"})


@router.get("/runs/{run_id}", responses=ERRORS, summary="A run's status and progress, then its result")
async def get_run(
    run_id: str = Path(..., max_length=32, pattern=r"^[0-9a-f]+$"),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    jobs = jobs_of(state)
    rec = jobs.get(run_id)
    if rec is None:
        raise NotFound(f"Unknown or expired run {run_id!r} (runs are kept {state.settings.forecast_retention_h} h)")
    return JSONResponse(jobs.view(rec), headers={"Cache-Control": "no-store"})


@router.get("/pages", summary="The precomputed page forecasts: which exist, their model and skill")
async def page_status(state: AppState = Depends(get_state)) -> JSONResponse:
    pf: PageForecasts | None = getattr(state, "page_forecasts", None)
    items: list[dict[str, Any]] = []
    for (vid, scope), e in sorted((pf.entries if pf else {}).items()):
        r = e.result or {}
        items.append(
            {
                "variable": vid,
                "label": VARS[vid].label,
                "pages": list(VARS[vid].pages),
                "scope": scope,
                "status": e.status,
                **({"error": e.error} if e.error else {}),
                **(
                    {
                        "model": r["model"]["id"],
                        "modelLabel": r["model"]["label"],
                        "skill": r.get("metrics", {}).get("skill"),
                        "mase": r.get("metrics", {}).get("mase"),
                        "dataAsOf": r.get("asOf"),
                        "runtimeSeconds": r.get("runtimeSeconds"),
                        "studio": e.studio,
                    }
                    if e.status == "done" and r
                    else {}
                ),
            }
        )
    return JSONResponse(
        {"newestData": pf.newest.isoformat() if pf and pf.newest else None, "items": items},
        headers={"Cache-Control": "no-store"},
    )
