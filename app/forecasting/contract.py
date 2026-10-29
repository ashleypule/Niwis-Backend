"""Contract endpoints of the AI & Predictive Analytics page (`ai`) and the Overview forecast panel that follow from the
page forecasts. Every other `ai` / `overview` path keeps answering 501 with its reason (app.modules.unavailable)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import get_current_user
from app.core.errors import DataUnavailable
from app.core.filters import parse_scope
from app.core.stats import rnd
from app.forecasting import pages as fc
from app.modules.common import Ctx, check_response, get_ctx
from app.modules.unavailable import reason
from app.schemas import contract as c

OPTS: dict[str, Any] = {"response_model_exclude_unset": True, "response_model_by_alias": True}
router = APIRouter(prefix="/api/v1", tags=["ai"], dependencies=[Depends(get_current_user)])
AI_SERIES = {
    "drought": ("spi-3", "SPI-3 forecast"),
    "streamflow": ("flow", "River discharge forecast"),
    "groundwater": ("gw-depth", "Depth to groundwater forecast"),
}
AI_UNAVAILABLE = {
    "reservoir": "Reservoir storage is withheld (the 8_1 storage values are a placeholder, DECISIONS D2), so it is not "
    "forecast.",
    "demand": "No water-demand data are in the database.",
    "quality": "The water-quality index is computed per district over 12 months; it is not forecast yet.",
    "scenario": "Climate scenarios (RCP 2.6 / 8.5) need projections, which are not in the database.",
}


@router.get("/ai/forecast", response_model=c.SeriesSet, include_in_schema=True, **OPTS)
async def ai_forecast(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    if metric in AI_SERIES:
        var, title = AI_SERIES[metric]
        return await check_response(
            ctx, request.url.path, fc.series_set(ctx, var, set_id=f"{metric}-forecast", title=title)
        )
    if metric in AI_UNAVAILABLE:
        raise DataUnavailable(AI_UNAVAILABLE[metric], missing=[f"forecast:ai:{metric}"])
    raise DataUnavailable(reason("ai", "forecast"), missing=[f"forecast:ai:{metric}"])


@router.get("/ai/table", response_model=c.TableSet, **OPTS)
async def ai_table(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    if metric == "models":
        return await check_response(ctx, request.url.path, models_table(ctx))
    if metric == "early-warning":
        raise DataUnavailable(
            "Early-warning signals need alert rules, which are not defined yet", missing=["table:ai:early-warning"]
        )
    raise DataUnavailable(reason("ai", "table"), missing=[f"table:ai:{metric}"])


@router.get("/overview/forecast", response_model=c.SeriesSet, **OPTS)
async def overview_forecast(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    if metric != "ai":
        raise DataUnavailable(reason("overview", "forecast"), missing=[f"forecast:overview:{metric}"])
    return await check_response(
        ctx, request.url.path, fc.series_set(ctx, "spi-3", set_id="ai-forecast", title="SPI-3 forecast")
    )


def models_table(ctx: Ctx) -> dict[str, Any]:
    """The page forecasts' models and backtest scores (South Africa, or the selected province)."""
    pf = fc.pages(ctx.state)
    scope = ctx.filters.scope if ctx.filters.scope.level in ("national", "province") else parse_scope()
    rows: list[dict[str, Any]] = []
    infos: list[c.ForecastInfo] = []
    for vid, v in fc.VARS.items():
        try:
            e = pf.entry(vid, scope)
        except DataUnavailable as exc:
            rows.append(
                {
                    "id": vid,
                    "variable": v.label,
                    "pages": ", ".join(v.pages),
                    "model": None,
                    "mae": None,
                    "rmse": None,
                    "mase": None,
                    "skill": None,
                    "coverage": None,
                    "dataAsOf": None,
                    "status": exc.error,
                }
            )
            continue
        r = e.result or {}
        met = r.get("metrics", {})
        info = fc.forecast_info(e, fc.PAGE_HORIZON)
        infos.append(info)
        rows.append(
            {
                "id": vid,
                "variable": v.label,
                "pages": ", ".join(v.pages),
                "model": r["model"]["label"],
                "mae": rnd(met.get("mae"), 3),
                "rmse": rnd(met.get("rmse"), 3),
                "mase": rnd(met.get("mase"), 2),
                "skill": rnd(met.get("skill"), 2),
                "coverage": rnd(met.get("coverage"), 0),
                "dataAsOf": r.get("asOf"),
                "status": "Beats climatology" if info.beats_climatology else "Does not beat the long-term average",
            }
        )
    if not infos:
        raise DataUnavailable(
            "The page forecasts are being computed; try again in a few minutes.", missing=["table:ai:models"]
        )
    body = c.TableSet(
        id="models",
        title=f"Page forecast models — backtest scores ({scope.name})",
        columns=[
            c.Column(key="variable", label="Variable", type="text"),
            c.Column(key="pages", label="Pages", type="text"),
            c.Column(key="model", label="Model (Auto)", type="text"),
            c.Column(key="mae", label="MAE", type="number", decimals=3, good_direction="down"),
            c.Column(key="rmse", label="RMSE", type="number", decimals=3, good_direction="down"),
            c.Column(key="mase", label="MASE", type="number", decimals=2, good_direction="down"),
            c.Column(key="skill", label="Skill vs climatology", type="number", decimals=2, good_direction="up"),
            c.Column(key="coverage", label="Band coverage", type="number", unit="%", decimals=0),
            c.Column(key="dataAsOf", label="Data to", type="date"),
            c.Column(key="status", label="Status", type="text"),
        ],
        rows=rows,
        row_key="id",
        as_of=min(i.data_as_of for i in infos),
        forecast=fc._combined(infos),
    )
    return body.dump()
