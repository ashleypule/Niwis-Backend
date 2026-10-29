"""The interface every forecasting model implements.

A model is fitted on one training window (`fit`) and forecasts the following `steps` periods (`predict`). Series are
pandas Series on a regular index (month starts for monthly data, days for daily data) without gaps: gaps are filled
inside the training window before `fit` (app.forecasting.validation.fill_gaps), never from later values.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

import numpy as np
import pandas as pd

Freq = Literal["monthly", "daily"]
Group = Literal["baseline", "statistical", "ml", "deep", "multivariate"]

GROUP_LABELS: dict[Group, str] = {
    "baseline": "Baselines",
    "statistical": "Statistical",
    "ml": "Machine learning",
    "deep": "Deep learning",
    "multivariate": "Multivariate",
}
PERIOD: dict[Freq, int] = {"monthly": 12, "daily": 365}
PANDAS_FREQ: dict[Freq, str] = {"monthly": "MS", "daily": "D"}
# 10th–90th percentile band, as the dashboard shows elsewhere
LOWER_Q, UPPER_Q = 0.10, 0.90
Z90 = 1.2815515655446004  # standard normal 90th percentile


class ModelError(Exception):
    """A model could not be fitted on this series (reported in the comparison table, never a crash)."""


@dataclass
class Prediction:
    mean: np.ndarray
    lower: np.ndarray | None = None  # 10th percentile, when the model has its own interval
    upper: np.ndarray | None = None  # 90th percentile


def future_index(last: pd.Timestamp, steps: int, freq: Freq) -> pd.DatetimeIndex:
    start = last + (pd.offsets.MonthBegin(1) if freq == "monthly" else pd.Timedelta(days=1))
    return pd.date_range(start, periods=steps, freq=PANDAS_FREQ[freq])


def season_key(index: pd.DatetimeIndex, freq: Freq) -> np.ndarray:
    """Calendar month (monthly) or day of year (daily; 29 Feb counts as 28 Feb) of each date."""
    if freq == "monthly":
        return np.asarray(index.month)
    md = index.month * 100 + index.day
    return np.asarray(np.where(md == 229, 228, md))


class ForecastModel(ABC):
    id: ClassVar[str]
    label: ClassVar[str]
    group: ClassVar[Group]
    description: ClassVar[str]
    min_obs: ClassVar[int] = 36  # minimum history in months (monthly runs)
    min_obs_daily: ClassVar[int] = 730  # minimum history in days (daily runs)
    supports_exog: ClassVar[bool] = False
    requires_exog: ClassVar[bool] = False  # a multivariate model needs at least one input variable
    resolutions: ClassVar[tuple[Freq, ...]] = ("monthly",)
    experimental: ClassVar[bool] = False
    native_interval: ClassVar[bool] = False  # predict() returns its own 10–90 % interval

    def __init__(self, freq: Freq = "monthly", seed: int = 0) -> None:
        self.freq = freq
        self.seed = seed
        self.last: pd.Timestamp | None = None
        self.fitted_params: dict[str, Any] = {}

    @property
    def period(self) -> int:
        return PERIOD[self.freq]

    @classmethod
    def minimum(cls, freq: Freq) -> int:
        return cls.min_obs if freq == "monthly" else cls.min_obs_daily

    def fit(self, y: pd.Series, exog: pd.DataFrame | None = None) -> ForecastModel:
        if y.isna().any():
            raise ModelError("training series has gaps (fill them first)")
        if exog is not None and not self.supports_exog:
            raise ModelError(f"{self.label} does not use input variables")
        if self.requires_exog and (exog is None or exog.empty):
            raise ModelError(f"{self.label} needs at least one input variable")
        self.last = pd.Timestamp(y.index[-1])
        self._fit(y.astype(float), exog)
        return self

    def predict(self, steps: int, exog_future: pd.DataFrame | None = None) -> Prediction:
        if self.last is None:
            raise ModelError("predict() before fit()")
        p = self._predict(steps, exog_future)
        if len(p.mean) != steps or not np.all(np.isfinite(p.mean)):
            raise ModelError(f"{self.label} produced no finite forecast")
        if (
            p.lower is not None
            and p.upper is not None
            and not (np.all(np.isfinite(p.lower)) and np.all(np.isfinite(p.upper)))
        ):
            p.lower = p.upper = None  # fall back to the backtest-residual interval
        return p

    def future_index(self, steps: int) -> pd.DatetimeIndex:
        assert self.last is not None
        return future_index(self.last, steps, self.freq)

    def params(self) -> dict[str, Any]:
        return dict(self.fitted_params)

    @abstractmethod
    def _fit(self, y: pd.Series, exog: pd.DataFrame | None) -> None: ...

    @abstractmethod
    def _predict(self, steps: int, exog_future: pd.DataFrame | None) -> Prediction: ...

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "id": cls.id,
            "label": cls.label,
            "group": cls.group,
            "groupLabel": GROUP_LABELS[cls.group],
            "description": cls.description,
            "minHistoryMonths": cls.min_obs,
            "minHistoryDays": cls.min_obs_daily if "daily" in cls.resolutions else None,
            "supportsInputs": cls.supports_exog,
            "requiresInputs": cls.requires_exog,
            "resolutions": list(cls.resolutions),
            "experimental": cls.experimental,
            "nativeInterval": cls.native_interval,
        }
