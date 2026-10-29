"""Statistical models: ETS (Holt-Winters), SARIMA with its order chosen by AIC, SARIMAX with input variables, Prophet.

Monthly series are modelled with yearly seasonality (period 12). Daily series (ETS and SARIMA only) are modelled as
anomalies from the training window's own day-of-year climatology, over the last three years of the window: a 365-day
seasonal ARIMA is not practical, and the climatology carries the seasonal cycle.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from app.forecasting.base import LOWER_Q, UPPER_Q, ForecastModel, Freq, ModelError, Prediction, season_key

DAILY_WINDOW = 3 * 365  # days a daily ETS / ARIMA is fitted on
ALPHA = 1 - (UPPER_Q - LOWER_Q)  # 0.2 → the 10th–90th percentile interval


class _Deseason:
    """Daily series → anomalies from the day-of-year mean of the training window (and back)."""

    def __init__(self, y: pd.Series) -> None:
        keys = season_key(pd.DatetimeIndex(y.index), "daily")
        self.means = pd.Series(y.to_numpy()).groupby(keys).mean()
        self.overall = float(y.mean())

    def clim(self, index: pd.DatetimeIndex) -> np.ndarray:
        return np.array([float(self.means.get(k, self.overall)) for k in season_key(index, "daily")])

    def remove(self, y: pd.Series) -> pd.Series:
        return pd.Series(y.to_numpy() - self.clim(pd.DatetimeIndex(y.index)), index=y.index)


def seasonal_strength(y: pd.Series, period: int) -> float:
    """Hyndman's seasonal strength F_s = max(0, 1 − var(remainder) / var(seasonal + remainder)) from an STL fit."""
    from statsmodels.tsa.seasonal import STL

    if len(y) < 2 * period + 1:
        return 0.0
    r = STL(y.to_numpy(), period=period, robust=True).fit()
    denom = float(np.var(r.seasonal + r.resid))
    return max(0.0, 1.0 - float(np.var(r.resid)) / denom) if denom > 0 else 0.0


def kpss_diffs(x: np.ndarray) -> int:
    """1 when the KPSS test rejects level stationarity at 5 %, else 0 (the auto-ARIMA convention)."""
    from statsmodels.tsa.stattools import kpss

    if np.std(x) == 0:
        return 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = kpss(x, regression="c", nlags="auto")[1]
    return 1 if p < 0.05 else 0


class ETS(ForecastModel):
    id = "ets"
    label = "ETS / Holt-Winters"
    group = "statistical"
    description = "Exponential smoothing with additive error and yearly seasonality; with or without a damped trend, "
    "chosen by AIC. Own 10–90 % interval."
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly", "daily")
    native_interval = True

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel

        self.deseason = None
        if self.freq == "daily":
            self.deseason = _Deseason(y)
            y = self.deseason.remove(y).iloc[-DAILY_WINDOW:]
        x = pd.Series(y.to_numpy(), index=pd.RangeIndex(len(y)))
        seasonal = "add" if self.freq == "monthly" and len(y) >= 2 * self.period + 2 else None
        best: tuple[float, Any, dict[str, Any]] | None = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for trend in (None, "add"):
                try:
                    m = ETSModel(
                        x,
                        error="add",
                        trend=trend,
                        damped_trend=trend is not None,
                        seasonal=seasonal,
                        seasonal_periods=self.period if seasonal else None,
                    )
                    r = m.fit(disp=False, maxiter=200)
                except Exception:  # noqa: S112 — a failed candidate is skipped; none fitting raises below
                    continue
                if np.isfinite(r.aic) and (best is None or r.aic < best[0]):
                    best = (float(r.aic), r, {"trend": "damped additive" if trend else "none", "seasonal": seasonal})
        if best is None:
            raise ModelError("no ETS variant could be fitted")
        self.res = best[1]
        self.n = len(x)
        self.fitted_params = {**best[2], "aic": round(best[0], 1)}

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pred = self.res.get_prediction(start=self.n, end=self.n + steps - 1)
            f = pred.summary_frame(alpha=ALPHA)
        mean = f["mean"].to_numpy()
        lo, hi = f["pi_lower"].to_numpy(), f["pi_upper"].to_numpy()
        if self.deseason is not None:
            c = self.deseason.clim(self.future_index(steps))
            mean, lo, hi = mean + c, lo + c, hi + c
        return Prediction(mean, lo, hi)


class SARIMA(ForecastModel):
    id = "sarima"
    label = "SARIMA (order by AIC)"
    group = "statistical"
    description = "Seasonal ARIMA. Differencing from tests (seasonal strength, KPSS); AR / MA orders from a small grid "
    "by AIC (4 non-seasonal × 1–2 seasonal candidates), refitted on every training window. Own 10–90 % interval."
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly", "daily")
    native_interval = True
    PQ: ClassVar[tuple[tuple[int, int], ...]] = ((1, 0), (0, 1), (1, 1), (2, 0))

    def _seasonal_grid(self, big_d: int) -> tuple[tuple[int, int], ...]:
        if self.freq == "daily":
            return ((0, 0),)
        return ((0, 1),) if big_d else ((1, 0), (1, 1))

    def _prepare(self, y: pd.Series) -> pd.Series:
        self.deseason = None
        if self.freq == "daily":
            self.deseason = _Deseason(y)
            y = self.deseason.remove(y).iloc[-DAILY_WINDOW:]
        return y

    def _orders(self, x: np.ndarray) -> tuple[int, int]:
        big_d = 0
        if self.freq == "monthly" and seasonal_strength(pd.Series(x), self.period) >= 0.64:
            big_d = 1
        base = x[self.period :] - x[: -self.period] if big_d else x
        return kpss_diffs(base), big_d

    def _fit_order(
        self, x: np.ndarray, exog: np.ndarray | None, order: tuple[int, int, int], sorder: tuple[int, int, int, int]
    ) -> Any:
        from statsmodels.tsa.statespace.sarimax import SARIMAX as SM

        trend = "c" if order[1] + sorder[1] == 0 else "n"
        m = SM(x, exog=exog, order=order, seasonal_order=sorder, trend=trend)
        return m.fit(disp=False, maxiter=60, method="lbfgs")

    def _search(self, x: np.ndarray, exog: np.ndarray | None) -> None:
        d, big_d = self._orders(x)
        best: tuple[float, Any, tuple[int, int, int], tuple[int, int, int, int]] | None = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for p, q in self.PQ:
                for sp, sq in self._seasonal_grid(big_d):
                    order = (p, d, q)
                    sorder = (sp, big_d, sq, self.period if self.freq == "monthly" else 0)
                    try:
                        r = self._fit_order(x, exog, order, sorder)
                    except Exception:  # noqa: S112 — a failed candidate order is skipped
                        continue
                    if np.isfinite(r.aic) and (best is None or r.aic < best[0]):
                        best = (float(r.aic), r, order, sorder)
        if best is None:
            raise ModelError("no SARIMA order could be fitted")
        self.res = best[1]
        o, s = best[2], best[3]
        self.fitted_params = {"order": list(o), "seasonalOrder": list(s), "aic": round(best[0], 1)}

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        y = self._prepare(y)
        self._search(y.to_numpy(), None)

    def _forecast(self, steps: int, exog_future: np.ndarray | None) -> Prediction:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fc = self.res.get_forecast(steps, exog=exog_future)
            mean = np.asarray(fc.predicted_mean)
            ci = np.asarray(fc.conf_int(alpha=ALPHA))
        lo, hi = ci[:, 0], ci[:, 1]
        if self.deseason is not None:
            c = self.deseason.clim(self.future_index(steps))
            mean, lo, hi = mean + c, lo + c, hi + c
        return Prediction(mean, lo, hi)

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        return self._forecast(steps, None)


class SARIMAX(SARIMA):
    id = "sarimax"
    label = "SARIMAX (with input variables)"
    group = "statistical"
    description = "SARIMA with the selected input variables as regressors. Future input values are the inputs' own "
    "forecasts (best statistical model per input), never their last value held constant."
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly",)
    supports_exog = True
    requires_exog = True

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        assert exog is not None
        self.deseason = None
        self.cols = list(exog.columns)
        self.mu, self.sd = exog.mean(), exog.std().replace(0, 1.0)
        z = ((exog - self.mu) / self.sd).to_numpy()
        self._search(y.to_numpy(), z)
        self.fitted_params["inputs"] = self.cols

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        if exog_future is None or len(exog_future) != steps:
            raise ModelError("SARIMAX needs future values of its inputs")
        z = ((exog_future[self.cols] - self.mu) / self.sd).to_numpy()
        return self._forecast(steps, z)


class Prophet(ForecastModel):
    id = "prophet"
    label = "Prophet"
    group = "statistical"
    description = "Additive trend + yearly seasonality (Meta's Prophet, monthly). Own 10–90 % interval."
    native_interval = True

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        logging.getLogger("cmdstanpy").setLevel(logging.ERROR)
        logging.getLogger("prophet").setLevel(logging.ERROR)
        from prophet import Prophet as P

        self.m = P(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            interval_width=UPPER_Q - LOWER_Q,
            uncertainty_samples=300,
        )
        df = pd.DataFrame({"ds": y.index, "y": y.to_numpy()})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.m.fit(df)
        self.fitted_params = {"seasonality": "yearly", "intervalWidth": UPPER_Q - LOWER_Q}

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        np.random.seed(self.seed)  # Prophet samples its interval with numpy's global generator
        f = self.m.predict(pd.DataFrame({"ds": self.future_index(steps)}))
        return Prediction(f["yhat"].to_numpy(), f["yhat_lower"].to_numpy(), f["yhat_upper"].to_numpy())
