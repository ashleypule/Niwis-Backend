"""Trend statistics (Sen's slope + Mann-Kendall) and small numeric helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pymannkendall as mk


@dataclass(frozen=True)
class Trend:
    slope: float  # per time step (e.g. per year for annual series)
    intercept: float  # Sen's intercept: fitted value at step 0
    p: float
    trend: str  # "increasing" | "decreasing" | "no trend"
    n: int


MIN_TREND_VALUES = 8


def trend_gap(values: list[float | None], what: str) -> str | None:
    """Why `sens_slope(values)` is None (too few values), or None when a trend can be estimated."""
    n = sum(1 for v in values if v is not None and math.isfinite(float(v)))
    if n >= MIN_TREND_VALUES:
        return None
    return f"A trend needs at least {MIN_TREND_VALUES} years of {what}; this scope and period have {n}"


def sens_slope(values: list[float | None]) -> Trend | None:
    """Sen's slope and Mann-Kendall test on an evenly spaced series. None if fewer than 8 values.

    Gaps are passed as NaN so step positions are preserved (pymannkendall ignores missing values).
    """
    x = np.array([np.nan if v is None else float(v) for v in values], dtype=float)
    if int(np.isfinite(x).sum()) < MIN_TREND_VALUES:
        return None
    r: Any = mk.original_test(x)
    return Trend(float(r.slope), float(r.intercept), float(r.p), str(r.trend), int(np.isfinite(x).sum()))


def trend_line(values: list[float | None]) -> list[float | None]:
    """Fitted Sen's-slope line at each step (None when there is no trend estimate)."""
    t = sens_slope(values)
    return [None] * len(values) if t is None else [t.intercept + t.slope * i for i in range(len(values))]


def rnd(v: float | None, nd: int = 3) -> float | None:
    """Round for payloads; NaN / None → None (contract: missing values are null, never 0)."""
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)
