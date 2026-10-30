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
from app.forecasting.registry import MODELS
from app.modules.common import Ctx, check_response, get_ctx
from app.modules.unavailable import reason
from app.schemas import contract as c
from app.state import AppState

FEATURES = [  # the lag features of the gradient-boosting model (app.forecasting.ml._features), in order
    "Last month",
    "2 months ago",
    "3 months ago",
    "6 months ago",
    "12 months ago",
    "3-month mean",
    "12-month mean",
    "Calendar month",
]
NOT_COMPUTED = "No page forecasts computed for this area yet (open a forecast tab to compute them)"

OPTS: dict[str, Any] = {"response_model_exclude_unset": True, "response_model_by_alias": True}
router = APIRouter(prefix="/api/v1", tags=["ai"], dependencies=[Depends(get_current_user)])
AI_SERIES = {
    "drought": ("spi-3", "SPI-3 forecast"),
    "streamflow": ("flow", "River discharge forecast"),
    "groundwater": ("gw-depth", "Depth to groundwater forecast"),
    "quality": ("wqi", "Water Quality Index forecast"),
}
AI_UNAVAILABLE = {
    "reservoir": "Reservoir storage is withheld (the 8_1 storage values are a placeholder, DECISIONS D2), so it is not "
    "forecast.",
    "demand": "No water-demand data are in the database.",
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


@router.get("/ai/summary", response_model=c.KpiSet, **OPTS)
async def ai_summary(request: Request, ctx: Ctx = Depends(get_ctx)) -> Any:
    """The AI page's KPIs from the page forecasts, the derived alerts and the run log (DECISIONS D47)."""
    return await check_response(ctx, request.url.path, await summary_kpis(ctx))


async def summary_kpis(ctx: Ctx) -> dict[str, Any]:
    from app.modules.alerts.service import AlertsService

    pf = fc.pages(ctx.state)
    scope = ctx.filters.scope
    done = [e for vid in fc.VARS if (e := pf.done(vid, scope)) is not None]
    infos = [fc.forecast_info(e, fc.PAGE_HORIZON) for e in done]
    models = {i.model for i in infos}
    skills = [i.skill for i in infos if i.skill is not None]
    as_of = max((i.data_as_of for i in infos), default=None)
    spi = pf.done("spi-3", scope)
    p6 = None
    if spi is not None and spi.result and len(spi.result.get("classes") or []) >= 6:
        p6 = float(spi.result["classes"][5]["pDrought"])
    svc = AlertsService(ctx)
    alerts = svc.in_scope(await svc.compute())
    runs = float(len(pf.jobs.started))
    when = as_of or (ctx.newest_db.isoformat() if ctx.newest_db else "")

    def kpi(
        kid: str,
        metric: str,
        label: str,
        value: float | None,
        unit: str | None,
        sublabel: str,
        *,
        decimals: int = 0,
        why: str | None = None,
    ) -> c.Kpi:
        if value is None:
            ctx.explain(f"kpis.{kid}", why or NOT_COMPUTED)
        return c.Kpi(
            id=kid,
            metric=metric,
            label=label,
            **({"sublabel": sublabel} if sublabel else {}),
            value=rnd(value, decimals),
            **({"unit": unit} if unit else {}),
            decimals=decimals,
            **({"as_of": as_of} if as_of else {}),
            **({"unavailable_reason": why} if value is None and why else {}),
        )

    kpis = [
        kpi(
            "models",
            "modelSkill",
            "Operational AI Models",
            float(len(models)) if done else None,
            "models",
            f"models Auto chose for the {len(done)} page forecasts of {scope.name}; {len(MODELS)} models in the "
            "Forecasting Studio",
        ),
        kpi(
            "skill",
            "modelSkill",
            "Mean Model Skill",
            sum(skills) / len(skills) if skills else None,
            None,
            "mean backtest skill against the same-month climatology over the page forecasts (> 0 beats the long-term "
            "average); not the Nash–Sutcliffe efficiency, which the engine does not compute",
            decimals=2,
        ),
        kpi(
            "drought6",
            "areaInDrought",
            "Drought Probability (6 months)",
            p6,
            "%",
            "probability of D1 or worse 6 months ahead from the SPI-3 forecast",
            why=None if spi is None else "The SPI-3 forecast carries no class probabilities",
        ),
        kpi(
            "reservoir",
            "storagePct",
            "Reservoir Outlook (12 months)",
            None,
            "% FSC",
            "",
            why=AI_UNAVAILABLE["reservoir"],
        ),
        kpi(
            "warnings",
            "damAlerts",
            "AI Early Warnings (Active)",
            float(len(alerts)),
            "alerts",
            "districts beyond a threshold in the latest complete period (the Alerts page's derived alerts)",
        ),
        kpi(
            "runs",
            "recordsIngested",
            "Forecast Runs (30 days)",
            runs,
            "runs",
            "Forecasting Studio and on-demand runs started in the last 30 days, counted since the API last started "
            "(runs are not stored in the database)",
        ),
    ]
    return c.KpiSet(module="ai", as_of=when, scope=ctx.scope, kpis=kpis).dump()


@router.get("/ai/breakdown", response_model=c.CategorySet, **OPTS)
async def ai_breakdown(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    if metric == "importance":
        return await check_response(ctx, request.url.path, await importance(ctx.state, ctx))
    raise DataUnavailable(reason("ai", "breakdown"), missing=[f"breakdown:ai:{metric}"])


async def importance(state: AppState, ctx: Ctx) -> dict[str, Any]:
    """Relative importance (%) of the gradient-boosting drought model's lag features, from a fit on the national
    SPI-3 series (cached until the data change). How much each feature drives the model's splits (LightGBM gain), not
    a cause-and-effect ranking."""
    import asyncio

    from app.forecasting.base import ModelError
    from app.forecasting.ml import MAX_LAG, GradientBoosting
    from app.forecasting.series import build_series

    async def load() -> dict[str, Any]:
        target = fc.VARS["spi-3"].make(state.registry)
        sd = await build_series(state, target, parse_scope(), "monthly")
        y = sd.values.interpolate(limit_area="inside").dropna()
        if len(y) <= MAX_LAG + 12:  # the lag features need more history than this (app.forecasting.ml)
            raise DataUnavailable(
                f"The national SPI-3 series ({len(y)} months) is too short to train the drought model",
                missing=["breakdown:ai:importance"],
            )

        def fit() -> list[float]:
            m = GradientBoosting()
            m.fit(y)
            imp = m.model.booster_.feature_importance(importance_type="gain")
            total = float(sum(imp)) or 1.0
            return [float(v) / total * 100 for v in imp[: len(FEATURES)]]

        try:
            values = await asyncio.to_thread(fit)
        except (ModelError, ImportError, OSError) as exc:  # OSError: LightGBM's shared library missing on the host
            raise DataUnavailable(
                f"The drought model cannot be trained on this server: {exc}", missing=["breakdown:ai:importance"]
            ) from exc
        return {
            "values": values,
            "asOf": sd.as_of.isoformat() if sd.as_of else None,
            "n": len(y),
            "sources": target.source,
        }

    r: dict[str, Any] = await state.cache.get_or_set(
        f"ai:importance:{ctx.newest_db}", state.settings.cache_ttl_long_series_s, load
    )
    return c.CategorySet(
        id="importance",
        title=f"Relative importance of the drought model's features (LightGBM on the national SPI-3, {r['n']} months)",
        unit="%",
        categories=FEATURES,
        series=[
            c.CategorySeries(
                key="importance", name="Relative importance (drought model)", values=[rnd(v, 1) for v in r["values"]]
            )
        ],
        as_of=r["asOf"],
        source=r["sources"],
        aggregation="district-area-weighted",
    ).dump()


@router.get("/ai/table", response_model=c.TableSet, **OPTS)
async def ai_table(request: Request, metric: str = Query(...), ctx: Ctx = Depends(get_ctx)) -> Any:
    if metric == "models":
        return await check_response(ctx, request.url.path, models_table(ctx))
    if metric == "early-warning":  # drought indicators: current class, 3-month class and its probability
        return await check_response(ctx, request.url.path, fc.early_warning_table(ctx))
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
