"""Every forecasting model, in the order the Studio lists them (`GET /api/v1/forecast/models`).

`auto` is not a model: it runs the candidates (every non-experimental model the series and inputs allow), picks the one
with the lowest backtest MASE and says why. `compare` runs every allowed model, experimental ones included, and keeps
the same winner rule; `auto-fast` keeps it over the quick models only (seconds per run). GARCH is deliberately
absent: it models volatility, not levels.
"""

from __future__ import annotations

from typing import Any

from app.forecasting.base import ForecastModel, Freq
from app.forecasting.baselines import Climatology, MovingAverage, Naive, SeasonalNaive
from app.forecasting.deep import LSTM, Transformer
from app.forecasting.ml import GradientBoosting, RandomForest
from app.forecasting.multivariate import VAR
from app.forecasting.statistical import ETS, SARIMA, SARIMAX, Prophet

MODELS: dict[str, type[ForecastModel]] = {
    m.id: m
    for m in (
        Naive,
        SeasonalNaive,
        Climatology,
        MovingAverage,
        ETS,
        SARIMA,
        SARIMAX,
        Prophet,
        RandomForest,
        GradientBoosting,
        LSTM,
        Transformer,
        VAR,
    )
}
BASELINES = ("naive", "seasonal-naive", "climatology", "moving-average")
AUTO, COMPARE, FAST = "auto", "compare", "auto-fast"
# `auto-fast`: Auto over the models that fit in seconds (the baselines, ETS and LightGBM; no SARIMA order search,
# Prophet, Random Forest or deep models) — what the district page forecasts and on-demand page forecasts use (D41)
FAST_MODELS = (*BASELINES, "ets", "lightgbm")
AUTO_CHOICES = (AUTO, COMPARE, FAST)
CHOICES = (*AUTO_CHOICES, *MODELS)


def allowed(model: type[ForecastModel], freq: Freq, n: int, has_inputs: bool) -> str | None:
    """Why `model` cannot run on this series (None: it can)."""
    if freq not in model.resolutions:
        return f"{model.label} runs on monthly series only"
    if n < model.minimum(freq):
        unit = "months" if freq == "monthly" else "days"
        return f"{model.label} needs at least {model.minimum(freq)} {unit} of history ({n} available)"
    if model.requires_exog and not has_inputs:
        return f"{model.label} needs at least one input variable"
    if model.group == "deep" and has_inputs:
        return f"{model.label} runs on single-target runs only (no input variables)"
    return None


def candidates(choice: str, freq: Freq, n: int, has_inputs: bool) -> list[str]:
    """Models a run fits: the chosen one plus the baselines, every allowed non-experimental model (auto), or every
    allowed model (compare). Exogenous models are candidates only when inputs are given."""
    if choice in AUTO_CHOICES:
        out = [
            k
            for k, m in MODELS.items()
            if allowed(m, freq, n, has_inputs) is None
            and (choice == COMPARE or not m.experimental)
            and (choice != FAST or k in FAST_MODELS)
        ]
        if has_inputs:  # a univariate model and its input-using twin both run; the backtest decides
            return out
        return [k for k in out if not MODELS[k].requires_exog]
    return list(dict.fromkeys([*[b for b in BASELINES if allowed(MODELS[b], freq, n, False) is None], choice]))


def catalogue() -> list[dict[str, Any]]:
    return [m.describe() for m in MODELS.values()]
