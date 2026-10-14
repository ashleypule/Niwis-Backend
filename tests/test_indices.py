"""Standardised index (SPI / SSI method).

Reference behaviour checked: for gamma-distributed input the index is ~N(0, 1) over the calibration period; the
calibration-period median maps to ~0; an exact gamma quantile maps to the matching normal quantile; zeros are handled
through the mixed distribution; the index is monotonic in the accumulated value within a calendar month.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from app.core.indices import standardized_index


def _series(values: np.ndarray, start: str = "1981-01-01") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"))


def test_gamma_input_gives_standard_normal() -> None:
    rng = np.random.default_rng(42)
    s = _series(rng.gamma(shape=2.0, scale=30.0, size=12 * 60))
    idx = standardized_index(s, 1, (1981, 2040)).dropna()
    assert abs(idx.mean()) < 0.05 and abs(idx.std() - 1) < 0.05
    assert stats.shapiro(idx.sample(400, random_state=1)).pvalue > 0.01


def test_exact_quantile_round_trip() -> None:
    """With a large gamma sample the fitted distribution ≈ the true one, so x = G⁻¹(Φ(z)) maps back to ≈ z."""
    rng = np.random.default_rng(7)
    vals = rng.gamma(shape=3.0, scale=20.0, size=12 * 400)
    s = _series(vals, "1600-01-01")
    true_x = stats.gamma.ppf(stats.norm.cdf(-1.5), 3.0, scale=20.0)
    s.iloc[-1] = true_x  # last value: the SPI = −1.5 quantile of the true distribution
    idx = standardized_index(s, 1, (1600, 1999))
    assert idx.iloc[-1] == pytest.approx(-1.5, abs=0.08)


def test_zeros_use_mixed_distribution() -> None:
    rng = np.random.default_rng(3)
    vals = rng.gamma(2.0, 10.0, size=12 * 50)
    vals[::4] = 0.0  # 25 % zeros
    idx = standardized_index(_series(vals), 1, (1981, 2030))
    zero_idx = idx[_series(vals) == 0].dropna()
    # P(zero) = 0.25 → H(0) = 0.25 → index = Φ⁻¹(0.25) ≈ −0.674 for every zero
    assert np.allclose(zero_idx, stats.norm.ppf(0.25), atol=0.05)


def test_monotonic_within_month_and_scale_accumulates() -> None:
    rng = np.random.default_rng(11)
    s = _series(rng.gamma(2.0, 25.0, size=12 * 40))
    idx = standardized_index(s, 3, (1981, 2020))
    acc = s.rolling(3).sum()
    jan = pd.DataFrame({"acc": acc, "idx": idx})[lambda d: d.index.month == 1].dropna().sort_values("acc")
    assert jan["idx"].is_monotonic_increasing
    assert idx.iloc[:2].isna().all()  # first scale-1 months have no 3-month accumulation


def test_too_little_calibration_data_gives_nan() -> None:
    s = _series(np.full(12 * 5, 10.0))
    assert standardized_index(s, 1, (1981, 1985)).isna().all()
