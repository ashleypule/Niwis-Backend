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
