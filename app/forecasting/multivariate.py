"""VAR on standardised monthly series: the target and the selected inputs, each standardised per calendar month over
the training window (mean and standard deviation of that month), so the seasonal cycle is removed before the VAR and
added back after. Lag order by AIC up to 6. Own 10–90 % interval (the VAR's, transformed back)."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from app.forecasting.base import LOWER_Q, UPPER_Q, ForecastModel, ModelError, Prediction

MAX_LAGS = 6


class VAR(ForecastModel):
    id = "var"
    label = "VAR (target + inputs)"
    group = "multivariate"
    description = "Vector autoregression of the target and the input variables, standardised per calendar month; lag "
    "order by AIC (≤ 6). Forecasts the inputs together with the target. Own 10–90 % interval."
    supports_exog = True
    requires_exog = True
    native_interval = True

    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None:
        from statsmodels.tsa.api import VAR as SM

        assert exog is not None
        df = pd.concat([y.rename("__target"), exog], axis=1)
        months = pd.DatetimeIndex(df.index).month
        self.mu = df.groupby(months).mean()
        self.sd = df.groupby(months).std().replace(0, np.nan).fillna(1.0)
        z = (df - self.mu.loc[months].to_numpy()) / self.sd.loc[months].to_numpy()
        if (z.std() == 0).any():
            raise ModelError("a VAR variable is constant after standardisation")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.res = SM(z.to_numpy()).fit(maxlags=MAX_LAGS, ic="aic")
        self.k = max(1, int(self.res.k_ar))
        if self.res.k_ar == 0:
            self.res = SM(z.to_numpy()).fit(1)
        self.z = z.to_numpy()
        self.fitted_params = {"lagOrder": int(self.res.k_ar), "inputs": list(exog.columns)}

    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction:
        months = self.future_index(steps).month
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mean, lo, hi = self.res.forecast_interval(self.z[-self.res.k_ar :], steps, alpha=1 - (UPPER_Q - LOWER_Q))
        mu = self.mu["__target"].loc[months].to_numpy()
        sd = self.sd["__target"].loc[months].to_numpy()
        return Prediction(mean[:, 0] * sd + mu, lo[:, 0] * sd + mu, hi[:, 0] * sd + mu)
