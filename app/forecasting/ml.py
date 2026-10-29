"""Machine learning: Random Forest and gradient boosting (LightGBM) on lag features — the spec's "RF / XGBoost" family
(§22.1). One-step models applied recursively: lags 1, 2, 3, 6 and 12 months, 3- and 12-month rolling means of past
values, the calendar month, and (with inputs) each input's value in the month and the month before. Future inputs are
the inputs' own forecasts. No own interval: the backtest residuals give it."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.forecasting.base import ForecastModel, ModelError, Prediction

LAGS = (1, 2, 3, 6, 12)
MAX_LAG = max(LAGS)


def _features(hist: np.ndarray, month: int, exog_now: np.ndarray | None, exog_prev: np.ndarray | None) -> list[float]:
    """Features for the period after `hist` (whose calendar month is `month`)."""
    f = [float(hist[-k]) for k in LAGS]
    f += [float(np.mean(hist[-3:])), float(np.mean(hist[-12:])), float(month)]
    if exog_now is not None and exog_prev is not None:
        f += [float(v) for v in exog_now] + [float(v) for v in exog_prev]
    return f


class _LagModel(ForecastModel):
    supports_exog = True

    def _make(self) -> Any:
        raise NotImplementedError

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        v = y.to_numpy()
        if len(v) <= MAX_LAG + 12:
            raise ModelError("too short for lag features")
        months = pd.DatetimeIndex(y.index).month.to_numpy()
        x = exog.to_numpy(dtype=float) if exog is not None else None
        rows, target = [], []
        for t in range(MAX_LAG, len(v)):
            rows.append(
                _features(v[:t], int(months[t]), x[t] if x is not None else None, x[t - 1] if x is not None else None)
            )
            target.append(v[t])
        self.model = self._make()
        self.model.fit(np.asarray(rows), np.asarray(target))
        self.hist = v.copy()
        self.exog_last = x[-1] if x is not None else None
        self.cols = list(exog.columns) if exog is not None else []
        self.fitted_params = {"lags": list(LAGS), "rolling": [3, 12], "inputs": self.cols, "trainingRows": len(rows)}

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        if self.cols and (exog_future is None or len(exog_future) != steps):
            raise ModelError(f"{self.label} needs future values of its inputs")
        hist = list(self.hist)
        months = self.future_index(steps).month.to_numpy()
        xf = exog_future[self.cols].to_numpy(dtype=float) if self.cols and exog_future is not None else None
        out = []
        prev = self.exog_last
        for i in range(steps):
            now = xf[i] if xf is not None else None
            f = _features(np.asarray(hist), int(months[i]), now, prev)
            yhat = float(self.model.predict(np.asarray([f]))[0])
            out.append(yhat)
            hist.append(yhat)
            prev = now
        return Prediction(np.asarray(out))


class RandomForest(_LagModel):
    id = "random-forest"
    label = "Random Forest"
    group = "ml"
    description = "Random Forest (300 trees) on lags 1, 2, 3, 6, 12, rolling means and the calendar month; applied "
    "recursively."

    def _make(self) -> Any:
        from sklearn.ensemble import RandomForestRegressor

        return RandomForestRegressor(n_estimators=300, min_samples_leaf=3, random_state=self.seed, n_jobs=1)


class GradientBoosting(_LagModel):
    id = "lightgbm"
    label = "Gradient boosting (LightGBM)"
    group = "ml"
    description = "Gradient-boosted trees (LightGBM, 300 rounds, learning rate 0.03) on the same lag features as the "
    "Random Forest; applied recursively."

    def _make(self) -> Any:
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            n_estimators=300,
            learning_rate=0.03,
            num_leaves=15,
            min_child_samples=10,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.9,
            random_state=self.seed,
            n_jobs=1,
            verbose=-1,
        )
