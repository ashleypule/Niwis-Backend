"""Standardised indices computed by the API when the database does not provide a usable one (e.g. SSI for runoff:
the DB's `ssi_dimensionless` is withheld because it derives from placeholder discharge).

Method (McKee et al. 1993; WMO-No. 1090 for SPI, applied to runoff as SSI): accumulate the monthly series over
`scale` months; for each calendar month, fit a two-parameter gamma distribution (location 0) to the non-zero
accumulations of the calibration years; the cumulative probability is H(x) = q + (1 − q)·G(x) where q is the share of
zeros; the index is the standard-normal quantile of H. Probabilities are clipped to [1e-6, 1 − 1e-6] (|index| ≤ 4.75).
A calendar month needs ≥ 10 non-zero calibration values, otherwise its index is NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

MIN_FIT = 10
P_CLIP = 1e-6


def calibration_gap(monthly: pd.Series, scale: int, calibration: tuple[int, int], name: str) -> str | None:
    """Why `standardized_index` is all-NaN for this series (too short a calibration record), or None."""
    s = monthly.sort_index().astype(float).rolling(scale, min_periods=scale).sum()
    cal = s[(s.index.year >= calibration[0]) & (s.index.year <= calibration[1])].dropna()
    best = max((int((cal[cal.index.month == m] > 0).sum()) for m in range(1, 13)), default=0)
    if best >= MIN_FIT:
        return None
    return (
        f"{name} needs at least {MIN_FIT} calibration years per calendar month ({calibration[0]}–{calibration[1]}); "
        f"this scope's record gives at most {best}"
    )


def standardized_index(monthly: pd.Series, scale: int, calibration: tuple[int, int]) -> pd.Series:
    """SPI-style index of a monthly series (DatetimeIndex at month starts, no gaps assumed)."""
    s = monthly.sort_index().astype(float)
    acc = s.rolling(scale, min_periods=scale).sum()
    out = pd.Series(np.nan, index=acc.index)
    cal = acc[(acc.index.year >= calibration[0]) & (acc.index.year <= calibration[1])]
    for month in range(1, 13):
        ref = cal[cal.index.month == month].dropna()
        nz = ref[ref > 0]
        if len(nz) < MIN_FIT:
            continue
        a, _, scale_ = stats.gamma.fit(nz.to_numpy(), floc=0)
        q = (ref == 0).mean()
        target = acc[acc.index.month == month].dropna()
        h = q + (1 - q) * stats.gamma.cdf(target.to_numpy(), a, loc=0, scale=scale_)
        out.loc[target.index] = stats.norm.ppf(np.clip(h, P_CLIP, 1 - P_CLIP))
    return out
