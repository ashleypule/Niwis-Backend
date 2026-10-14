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
    p: float
    trend: str  # "increasing" | "decreasing" | "no trend"
    n: int


def sens_slope(values: list[float | None]) -> Trend | None:
    """Sen's slope and Mann-Kendall test on an evenly spaced series (missing values dropped). None if n < 8."""
    x = np.array([v for v in values if v is not None and not math.isnan(v)], dtype=float)
    if len(x) < 8:
        return None
    r: Any = mk.original_test(x)
    return Trend(float(r.slope), float(r.p), str(r.trend), len(x))


def rnd(v: float | None, nd: int = 3) -> float | None:
    """Round for payloads; NaN / None → None (contract: missing values are null, never 0)."""
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)
