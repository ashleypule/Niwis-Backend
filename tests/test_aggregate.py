"""Aggregation core: area weighting, the 95 % coverage rule, class areas, WMA fractions, normals."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.core.aggregate import (
    class_areas,
    monthly_normals,
    sum_fractions,
    weighted,
    weighted_scalar,
    window_normal,
)
from app.core.filters import parse_scope
from app.core.spatial import get_spatial

W = {"A": 3.0, "B": 1.0}


def test_weighted_scalar_is_area_weighted_not_plain_mean() -> None:
    assert weighted_scalar(pd.Series({"A": 10.0, "B": 30.0}), W) == pytest.approx(15.0)  # plain mean would be 20


def test_coverage_rule() -> None:
    # B missing → only 75 % of the weight has data → no value (never a mean of what happens to be there)
    assert weighted_scalar(pd.Series({"A": 10.0}), W) is None
    df = pd.DataFrame({"district": ["A", "A", "B"], "date": [1, 2, 1], "value": [10.0, 20.0, 30.0]})
    out = weighted(df, W).set_index("date")
    assert out.loc[1, "value"] == pytest.approx(15.0) and out.loc[1, "coverage"] == 1.0
    assert pd.isna(out.loc[2, "value"]) and out.loc[2, "coverage"] == 0.75


def test_ignores_districts_outside_scope() -> None:
    assert weighted_scalar(pd.Series({"A": 10.0, "B": 30.0, "Z": 1000.0}), W) == pytest.approx(15.0)


def test_class_areas() -> None:
    cls = pd.Series({"A": "D1", "B": "None", "Z": "D4"})
    assert class_areas(cls, W) == {"D1": 3.0, "None": 1.0}


def test_sum_fractions() -> None:
    assert sum_fractions(parse_scope(province="WC"), get_spatial()) == dict.fromkeys(
        ("DC1", "DC2", "DC3", "DC4", "DC5", "CPT"), 1.0
    )
    s = get_spatial()
    f = sum_fractions(parse_scope(wma="WMA9", wma_districts=s.wma_districts()), s)
    assert 0.95 < f["CPT"] <= 1.0  # Cape Town lies inside Berg-Olifants
    assert all(0 < v <= 1.0 + 1e-9 for v in f.values())


def test_monthly_normals_and_prorating() -> None:
    dates = pd.to_datetime(["2024-09-01", "2025-09-01"])
    total = pd.DataFrame({"district": ["A", "A"], "date": dates, "value": [40.0, 70.0]})
    anom = pd.DataFrame({"district": ["A", "A"], "date": dates, "value": [-15.0, 15.0]})
    n = monthly_normals(total, anom)
    assert n.to_dict("records") == [{"district": "A", "month": 9, "normal": 55.0}]
    # 6 of 30 September days → one fifth of the monthly normal
    assert window_normal(n, dt.date(2026, 9, 1), dt.date(2026, 9, 6))["A"] == pytest.approx(11.0)


def test_period_gap_requires_the_whole_period() -> None:
    from app.core.aggregate import period_gap

    # the bug behind BACKEND_ISSUES #6: data from 2000 "covered" 1981–2000 because the year 2000 is inside it
    assert period_gap("1981–2000", 1981, 2000, 2000, 2026) == (
        "The database starts in 2000; the 1981–2000 period needs data from 1981"
    )
    assert period_gap("2001–2020", 2001, 2020, 2000, 2026) is None
    assert period_gap("2021–2026", 2021, 2026, 2024, 2026) is not None  # fixture DB: daily data from 2024
    assert period_gap("2001–2020", 2001, 2020, 2000, 2015) == (
        "The database ends in 2015; the 2001–2020 period needs data to 2020"
    )
    assert period_gap("2001–2020", 2001, 2020, None, None) == "No data for this scope in 2001–2020"


# ---------------------------------------------------------------- area-based drought class (B4)


def _classes(**areas: tuple[str, float]) -> tuple[pd.Series, dict[str, float]]:
    """district → (class, area km²)."""
    return pd.Series({k: v[0] for k, v in areas.items()}), {k: v[1] for k, v in areas.items()}


def test_area_class_regional_drought_is_not_averaged_away() -> None:
    from app.core.aggregate import area_class

    cls, w = _classes(a=("D1", 85.0), b=("None", 15.0))  # KZN: 85 % in D1, mean SPI may say "No drought"
    ac = area_class(cls, w, 20)
    assert (ac.cls, ac.rank_text, round(ac.in_drought or 0)) == ("D1", "D1", 85)


def test_area_class_is_cumulative_worst_first() -> None:
    from app.core.aggregate import area_class

    cls, w = _classes(a=("D3", 15.0), b=("D4", 10.0), c=("None", 75.0))
    assert area_class(cls, w, 20).cls == "D3"  # 10 % D4 alone < 20 %, D3 or worse = 25 %
    assert area_class(cls, w, 10).cls == "D4"
    assert area_class(cls, w, 30).cls == "None"  # nothing reaches 30 %, not even D0 or worse (25 %)


def test_area_class_no_drought_and_coverage() -> None:
    from app.core.aggregate import area_class

    cls, w = _classes(a=("D2", 10.0), b=("None", 90.0))
    ac = area_class(cls, w, 20)
    assert (ac.cls, ac.rank_text, ac.in_drought) == ("None", "No drought", 10.0)
    cls, w = _classes(a=("D2", 50.0))
    assert area_class(cls, {**w, "missing": 50.0}, 20).cls is None  # < 95 % of the area has data
