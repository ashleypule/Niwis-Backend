"""Baselines: always run, so every model is compared with something simple. None has its own interval (the backtest
residuals give it)."""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from app.forecasting.base import ForecastModel, Freq, Prediction, season_key


class Naive(ForecastModel):
    id = "naive"
    label = "Naive (last value)"
    group = "baseline"
    description = "Every future period equals the last observed value."
    min_obs = 12
    min_obs_daily = 30
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly", "daily")

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        self.value = float(y.iloc[-1])

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        return Prediction(np.full(steps, self.value))


class SeasonalNaive(ForecastModel):
    id = "seasonal-naive"
    label = "Seasonal naive (same period last year)"
    group = "baseline"
    description = "Each future month equals the same month a year earlier (daily: the same day a year earlier)."
    min_obs = 12
    min_obs_daily = 365
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly", "daily")

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        self.tail = y.to_numpy()[-self.period :]

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        reps = int(np.ceil(steps / self.period))
        return Prediction(np.tile(self.tail, reps)[:steps])


class Climatology(ForecastModel):
    id = "climatology"
    label = "Climatology (same-month mean)"
    group = "baseline"
    description = "Each future month equals the mean of the same calendar month over the training years (daily: the "
    "same day of the year)."
    min_obs = 12
    min_obs_daily = 365
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly", "daily")

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        keys = season_key(pd.DatetimeIndex(y.index), self.freq)
        self.means = pd.Series(y.to_numpy()).groupby(keys).mean()
        self.overall = float(y.mean())
        self.fitted_params = {"years": round(len(y) / self.period, 1)}

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        keys = season_key(self.future_index(steps), self.freq)
        return Prediction(np.array([float(self.means.get(k, self.overall)) for k in keys]))


class MovingAverage(ForecastModel):
    id = "moving-average"
    label = "12-month moving average"
    group = "baseline"
    description = "Every future period equals the mean of the last 12 months (daily: the last 30 days)."
    min_obs = 12
    min_obs_daily = 30
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly", "daily")

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        n = 12 if self.freq == "monthly" else 30
        self.value = float(y.iloc[-n:].mean())
        self.fitted_params = {"window": n}

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        return Prediction(np.full(steps, self.value))
