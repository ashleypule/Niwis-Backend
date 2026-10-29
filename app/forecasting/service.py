"""What the Forecasting Studio API needs around the engine: the list of forecastable columns, request validation, and
turning a request into a worker payload (series read from the database here, in the API process)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import BadRequest, Unprocessable
from app.core.filters import ScopeSel, parse_scope
from app.forecasting.base import Freq
from app.forecasting.engine import MAX_HORIZON, RunSpec, precheck
from app.forecasting.jobs import ForecastJobs, RunRecord
from app.forecasting.registry import CHOICES, MODELS
from app.forecasting.series import (
    FORECASTABLE,
    SeriesData,
    Target,
    build_series,
    scope_label,
    target_from_catalogue,
    why_not_forecastable,
)
from app.forecasting.validation import SeriesError
from app.modules.categories import CategoryService
from app.modules.common import Ctx
from app.registry.catalogue import get_catalogue, monthly_unit
from app.state import AppState

DISCLAIMER = "Statistical forecast from historical data; not an official SAWS / DWS outlook."
NO_SKILL = "This model does not beat the long-term average for this variable."
MAX_INPUTS = 3


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["national", "province", "district", "wma"] = "national"
    id: str | None = Field(None, max_length=16, description="Province code, district MDB code or WMA id")


class ColumnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(max_length=120)
    column: str = Field(max_length=120)


class RunIn(BaseModel):
    """A forecast request (`POST /api/v1/forecast/runs`)."""

    model_config = ConfigDict(extra="forbid")
    table: str = Field(max_length=120, description="Catalogue table, e.g. 6.2_rainfall_derived_drought_indices_master")
    column: str = Field(max_length=120, description="A live numeric column of that table")
    scope: ScopeIn = Field(default_factory=ScopeIn)
    model: str = Field("auto", max_length=32, description="auto, compare, or a model id from /forecast/models")
    horizon: int = Field(12, ge=1, le=30, description="Months (1–24) or, for daily runs, days (1–30)")
    inputs: list[ColumnIn] = Field(default_factory=list, max_length=MAX_INPUTS)
    resolution: Literal["monthly", "daily"] = "monthly"


def scope_of(state: AppState, s: ScopeIn) -> ScopeSel:
    if s.level != "national" and not s.id:
        raise BadRequest(f"scope.id is required for a {s.level}", issues=[{"param": "scope.id"}])
    kw: dict[str, str | None] = (
        {"province": s.id} if s.level == "province" else {"wma": s.id} if s.level == "wma" else {}
    )
    if s.level == "district":
        kw = {"municipality": s.id}
    return parse_scope(**kw, wma_districts=state.spatial.wma_districts())


def to_payload(s: pd.Series) -> dict[str, Any]:
    return {
        "index": [t.date().isoformat() for t in s.index],
        "values": [None if pd.isna(v) else round(float(v), 6) for v in s.to_numpy()],
    }


def scope_key(scope: ScopeSel) -> dict[str, Any]:
    level = "district" if scope.level == "municipality" else scope.level
    return {"level": level, **({"id": scope.id} if level != "national" else {})}


async def prepare(
    state: AppState,
    target: Target,
    scope: ScopeSel,
    *,
    model: str,
    horizon: int,
    freq: Freq,
    inputs: list[Target] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(normalised request, worker payload, result context). Raises 400 / 422 before anything is queued."""
    if model not in CHOICES:
        raise BadRequest(f"Unknown model {model!r}", issues=[{"param": "model", "allowed": list(CHOICES)}])
    if horizon < 1 or horizon > MAX_HORIZON[freq]:
        unit = "months" if freq == "monthly" else "days"
        raise BadRequest(
            f"horizon must be 1–{MAX_HORIZON[freq]} {unit} for {freq} forecasts",
            issues=[{"param": "horizon", "min": 1, "max": MAX_HORIZON[freq]}],
        )
    inputs = inputs or []
    if inputs and freq == "daily":
        raise BadRequest("Input variables are supported for monthly forecasts only", issues=[{"param": "inputs"}])
    if any((t.table, t.column) == (target.table, target.column) for t in inputs):
        raise BadRequest("An input cannot be the target itself", issues=[{"param": "inputs"}])
    y = await build_series(state, target, scope, freq)
    xs: dict[str, SeriesData] = {}
    for t in inputs:
        xs[f"{t.table}.{t.column}"] = await build_series(state, t, scope, "monthly")
    if y.values.empty:
        raise Unprocessable(f"{target.label} has no values for {scope.name}", issues=[{"param": "scope"}])
    hist_min = float(y.values.min())
    low, high = target.bounds(hist_min)
    labels = {f"{t.table}.{t.column}": t.label for t in inputs}
    spec = RunSpec(
        freq=freq, horizon=horizon, model=model, classify=target.classify, clip_low=low, clip_high=high, labels=labels
    )
    try:
        check = precheck(y.values, {k: v.values for k, v in xs.items()}, spec)
    except SeriesError as exc:
        raise Unprocessable(str(exc), issues=[{"param": "table/column", "reason": str(exc)}]) from exc
    request = {
        "table": target.table,
        "column": target.column,
        **({"expr": target.expr_id} if target.expr_id else {}),
        "scope": scope_key(scope),
        "model": model,
        "horizon": horizon,
        "inputs": [{"table": t.table, "column": t.column} for t in inputs],
        "resolution": freq,
    }
    payload = {
        "target": to_payload(y.values),
        "inputs": {k: to_payload(v.values) for k, v in xs.items()},
        "spec": {
            "freq": freq,
            "horizon": horizon,
            "model": model,
            "classify": target.classify,
            "clip_low": low,
            "clip_high": high,
            "labels": labels,
        },
    }
    context = {
        "label": "Statistical forecast",
        "disclaimer": DISCLAIMER,
        "target": {
            "table": target.table,
            "column": target.column,
            "label": target.label,
            "unit": y.unit,
            "resolution": freq,
            "aggregate": target.aggregate,
            "classified": target.classify,
            "note": y.note,
        },
        "scope": scope_label(scope),
        "inputs": [
            {"table": t.table, "column": t.column, "label": t.label, "unit": xs[f"{t.table}.{t.column}"].unit}
            for t in inputs
        ],
        "sources": sorted({*target.source, *(s for t in inputs for s in t.source)}),
        "dataAsOf": y.as_of.isoformat() if y.as_of else None,
        "plan": {k: v for k, v in check.items() if k != "warnings"},
    }
    return request, payload, context


async def submit(state: AppState, jobs: ForecastJobs, body: RunIn, ip: str) -> tuple[RunRecord, bool]:
    target = target_from_catalogue(body.table, body.column)
    inputs = [target_from_catalogue(i.table, i.column) for i in body.inputs]
    for t in inputs:
        if t.resolution not in ("daily", "monthly"):
            raise BadRequest(f"Input {t.label} cannot be used", issues=[{"param": "inputs"}])
    scope = scope_of(state, body.scope)
    request, payload, context = await prepare(
        state, target, scope, model=body.model, horizon=body.horizon, freq=body.resolution, inputs=inputs
    )
    return jobs.submit(request, payload, context, ip=ip)


# ---------------------------------------------------------------- listings


def models_listing(settings: Any) -> dict[str, Any]:
    from app.forecasting.base import GROUP_LABELS
    from app.forecasting.registry import catalogue

    return {
        "choices": [
            {
                "id": "auto",
                "label": "Auto (best by backtest)",
                "description": "Runs every non-experimental model the series allows and keeps the one with the lowest "
                "backtest MASE; the comparison table shows all of them.",
            },
            {
                "id": "compare",
                "label": "Compare all",
                "description": "Runs every model the series allows, experimental ones included, and keeps the best.",
            },
        ],
        "groups": [{"id": k, "label": v} for k, v in GROUP_LABELS.items()],
        "models": catalogue(),
        "limits": {
            "monthlyHorizon": [1, MAX_HORIZON["monthly"]],
            "dailyHorizon": [1, MAX_HORIZON["daily"]],
            "minHistoryMonths": 36,
            "minHistoryMonthsDeep": max(m.min_obs for m in MODELS.values()),
            "minHistoryDays": 730,
            "maxInputs": MAX_INPUTS,
            "runsPerHour": settings.forecast_runs_per_hour,
            "timeoutSeconds": settings.forecast_timeout_s,
            "retentionHours": settings.forecast_retention_h,
        },
        "disclaimer": DISCLAIMER,
    }


async def targets_listing(ctx: Ctx, module: str | None) -> dict[str, Any]:
    """Every live numeric daily / monthly column (optionally of one module's tables) with label, unit, resolution,
    aggregation and available history."""
    cats = get_catalogue().for_module(module) if module else get_catalogue().categories
    if module and not cats:
        modules = sorted({m for c in get_catalogue().categories for m in c.modules})
        raise BadRequest(f"Unknown module {module!r}", issues=[{"param": "module", "allowed": modules}])
    first_dates = {
        it["table"]: it.get("firstDate") for it in (await ctx.state.meta.sources())["items"] if it.get("firstDate")
    }
    tables: list[dict[str, Any]] = []
    for cat in cats:
        svc = CategoryService(ctx, cat.modules[0])
        await svc.refs()
        dates = await svc.column_dates(cat)
        ds = svc.dataset(cat)
        items: list[dict[str, Any]] = []
        for x in cat.columns:
            if x.kind != "numeric" or x.resolution not in FORECASTABLE:
                continue
            why = why_not_forecastable(x)
            if why:
                continue
            last = svc.as_of(x, dates, ds)
            reg = ds.columns.get(x.column)
            first_s = first_dates.get(cat.table)
            first = dt.date.fromisoformat(first_s) if first_s else None
            if reg is not None and reg.valid_from and (first is None or reg.valid_from > first):
                first = reg.valid_from
            if reg is not None and reg.valid_until and last is not None:
                last = min(last, reg.valid_until)
            months = (last.year - first.year) * 12 + last.month - first.month + 1 if first and last else None
            agg = x.aggregate or "mean"
            t = target_from_catalogue(cat.table, x.column)
            items.append(
                {
                    "table": cat.table,
                    "column": x.column,
                    "label": x.label,
                    "name": x.name,
                    "unit": x.unit,
                    "monthlyUnit": monthly_unit(x.unit, agg, x.resolution),
                    "resolution": x.resolution,
                    "resolutions": t.resolutions(),
                    "aggregate": agg,
                    "classified": t.classify,
                    "firstDate": first.isoformat() if first else None,
                    "lastDate": last.isoformat() if last else None,
                    "historyMonths": months,
                    "enoughHistory": bool(months and months >= 36),
                    **({"note": x.note} if x.note else {}),
                }
            )
        if items:
            tables.append(
                {
                    "table": cat.table,
                    "id": cat.id,
                    "label": cat.label,
                    "spec": cat.spec,
                    "modules": list(cat.modules),
                    "columns": items,
                }
            )
    return {
        "module": module,
        "tables": tables,
        "count": sum(len(t["columns"]) for t in tables),
        "note": "Live numeric columns with a daily or monthly resolution. Withheld and duplicate columns cannot be "
        "forecast; annual columns hold too few values.",
    }
