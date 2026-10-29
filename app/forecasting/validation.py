"""Validation done chronologically (DECISIONS D33).

- Gap filling happens inside each training window, from that window only (`fill_gaps`).
- Rolling-origin backtest: origins o₁ < … < o_K; at each origin the model is fitted on the periods before it and
  forecasts the next h periods. Nothing after an origin is visible to that origin's forecast (the leakage test changes
  later data and checks the prediction is identical).
- Metrics over the backtest: MAE, RMSE, MASE (each origin's MAE divided by the in-sample MAE of the seasonal naive
  forecast on its training window, then averaged), skill against climatology (1 − SSE / SSE of the climatology
  baseline on the same points), sMAPE for series that reach zero or change sign (else MAPE) and the share of actuals
  inside the 10–90 % band.
- Intervals: the model's own where it has one; otherwise the 10th / 90th percentiles of the backtest errors at that
  lead time (pooled over neighbouring lead times, ±2), widening monotonically with the horizon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.forecasting.base import LOWER_Q, PERIOD, UPPER_Q, Freq, season_key

LEAD_POOL = 2  # residual intervals pool the errors of lead times h − 2 … h + 2


class SeriesError(ValueError):
    """The series cannot be forecast as requested (→ 422 with this reason)."""


def fill_gaps(y: pd.Series, freq: Freq) -> pd.Series:
    """Leading / trailing gaps trimmed; interior gaps = the window's own same-period mean plus the anomaly
    interpolated linearly between the neighbouring observed periods (both inside the window)."""
    first, last = y.first_valid_index(), y.last_valid_index()
    if first is None or last is None:
        raise SeriesError("the training window holds no values")
    y = y.loc[first:last]
    if not y.isna().any():
        return y
    keys = season_key(pd.DatetimeIndex(y.index), freq)
    clim = pd.Series(y.to_numpy()).groupby(keys).mean()
    base = np.array([clim.get(k, np.nan) for k in keys], dtype=float)
    base = np.where(np.isnan(base), float(y.mean()), base)
    anom = pd.Series(y.to_numpy() - base, index=y.index).interpolate(method="linear", limit_area="inside")
    return pd.Series(anom.to_numpy() + base, index=y.index)


def plan_origins(n: int, horizon: int, min_train: int, freq: Freq, k_max: int = 4, k_min: int = 3) -> list[int]:
    """Backtest origins (index of the first forecast period), oldest first. Origins are `step` apart (h, at most a
    year for monthly / a week for daily data), so later origins see more history; the last origin's forecast ends with
    the last observation. Raises when not even `k_min` origins leave `min_train` periods of training data."""
    step = max(1, min(horizon, 12 if freq == "monthly" else 7))
    for k in range(k_max, k_min - 1, -1):
        first = n - horizon - (k - 1) * step
        if first >= min_train:
            return [first + i * step for i in range(k)]
    unit = "months" if freq == "monthly" else "days"
    need = min_train + horizon + (k_min - 1) * step
    raise SeriesError(
        f"A {k_min}-origin backtest at a {horizon}-{unit[:-1]} horizon needs {need} {unit} of history "
        f"({min_train} to train, then {k_min} forecasts); this series has {n}. Choose a shorter horizon."
    )


@dataclass
class OriginResult:
    origin: pd.Timestamp
    index: pd.DatetimeIndex
    actual: np.ndarray
    pred: np.ndarray
    lower: np.ndarray | None
    upper: np.ndarray | None
    scale: float  # in-sample seasonal-naive MAE of the training window (MASE denominator)


@dataclass
class Backtest:
    model: str
    origins: list[OriginResult] = field(default_factory=list)
    native: bool = False

    def errors(self) -> np.ndarray:
        """origin × lead-time matrix of forecast − actual (NaN where the actual is missing)."""
        return np.vstack([o.pred - o.actual for o in self.origins])

    def points(self) -> tuple[np.ndarray, np.ndarray]:
        a = np.concatenate([o.actual for o in self.origins])
        p = np.concatenate([o.pred for o in self.origins])
        ok = np.isfinite(a)
        return a[ok], p[ok]


def naive_scale(train: np.ndarray, freq: Freq) -> float:
    """In-sample MAE of the seasonal naive forecast (lag 12 / 365); lag 1 if the window is shorter than a year."""
    m = PERIOD[freq] if len(train) > PERIOD[freq] else 1
    d = np.abs(train[m:] - train[:-m])
    s = float(np.mean(d)) if len(d) else 0.0
    return s if s > 0 else float("nan")


def residual_band(errors: np.ndarray, horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """(lower, upper) offsets per lead time from backtest errors (forecast − actual): quantiles of actual − forecast
    pooled over lead times h ± 2, then made non-narrowing with the horizon."""
    lo, hi = np.zeros(horizon), np.zeros(horizon)
    resid = -errors  # actual − forecast: the offset to add to a forecast
    for h in range(horizon):
        a, b = max(0, h - LEAD_POOL), min(horizon, h + LEAD_POOL + 1)
        r = resid[:, a:b].ravel()
        r = r[np.isfinite(r)]
        if len(r) == 0:
            r = resid[np.isfinite(resid)]
        if len(r) == 0:
            continue
        lo[h], hi[h] = float(np.quantile(r, LOWER_Q)), float(np.quantile(r, UPPER_Q))
    return np.minimum.accumulate(lo), np.maximum.accumulate(hi)


def bands_for(bt: Backtest, horizon: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Each origin's 10–90 % band: the model's own, or residual-based from the *other* origins' errors
    (leave-one-origin-out, so an origin's coverage is never judged with its own errors)."""
    if bt.native and all(o.lower is not None and o.upper is not None for o in bt.origins):
        return [(o.lower, o.upper) for o in bt.origins]  # type: ignore[misc]
    err = bt.errors()
    out = []
    for i, o in enumerate(bt.origins):
        others = np.delete(err, i, axis=0) if len(bt.origins) > 1 else err
        lo, hi = residual_band(others, horizon)
        out.append((o.pred + lo[: len(o.pred)], o.pred + hi[: len(o.pred)]))
    return out


def metrics(bt: Backtest, clim: Backtest | None, horizon: int, signed: bool) -> dict[str, Any]:
    a, p = bt.points()
    if len(a) == 0:
        return {}
    err = p - a
    mae, rmse = float(np.mean(np.abs(err))), float(np.sqrt(np.mean(err**2)))
    per_origin = []
    for o in bt.origins:
        ok = np.isfinite(o.actual)
        if ok.any() and np.isfinite(o.scale):
            per_origin.append(float(np.mean(np.abs(o.pred[ok] - o.actual[ok]))) / o.scale)
    mase = float(np.mean(per_origin)) if per_origin else None
    skill = None
    if clim is not None:
        ca, cp = clim.points()
        sse_c = float(np.sum((cp - ca) ** 2))
        if sse_c > 0 and len(ca) == len(a):
            skill = 1.0 - float(np.sum(err**2)) / sse_c
    out: dict[str, Any] = {"mae": mae, "rmse": rmse, "mase": mase, "skill": skill, "n": len(a)}
    if signed:
        denom = np.abs(a) + np.abs(p)
        ok = denom > 0
        out["smape"] = float(np.mean(2 * np.abs(err[ok]) / denom[ok]) * 100) if ok.any() else None
    else:
        out["mape"] = float(np.mean(np.abs(err) / np.abs(a)) * 100)
    inside = total = 0
    for o, (lo, hi) in zip(bt.origins, bands_for(bt, horizon), strict=True):
        ok = np.isfinite(o.actual)
        inside += int(np.sum((o.actual[ok] >= lo[ok]) & (o.actual[ok] <= hi[ok])))
        total += int(ok.sum())
    out["coverage"] = inside / total * 100 if total else None
    return out
