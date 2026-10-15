"""District → scope aggregation (area weights) and climatological normals.

Weights (decision D1/D2):
- municipality (one district): weight 1, aggregation "district";
- province / national: district area (km², reference/geo/districts.json), "district-area-weighted";
- WMA: area of each district inside the WMA, "district-overlap" (approximate).

Means are Σ wᵢ·xᵢ / Σ wᵢ over districts with data; a period is reported only when ≥ 95 % of the scope's weight has data.
Percentages are never averaged: anomalies are recomputed from aggregated totals and normals.
"""

from __future__ import annotations

import calendar
import datetime as dt

import pandas as pd

from app.core.errors import DataUnavailable
from app.core.filters import ScopeSel
from app.core.spatial import Spatial

MIN_COVERAGE = 0.95


def weights(scope: ScopeSel, spatial: Spatial) -> dict[str, float]:
    if scope.level == "municipality":
        return {scope.id: 1.0}
    if scope.level == "wma":
        ov = spatial.wma_overlap()
        if ov is None:
            raise DataUnavailable("WMA aggregation needs district boundaries", missing=["district boundaries"])
        return dict(ov.get(scope.id, {}))
    areas = spatial.district_areas()
    if areas is None:
        raise DataUnavailable("Area-weighted aggregation needs district boundaries", missing=["district boundaries"])
    return {c: areas[c] for c in scope.district_codes}


def sum_fractions(scope: ScopeSel, spatial: Spatial) -> dict[str, float]:
    """Fraction of each district's volume that belongs to the scope (for sums such as dam storage or runoff volume).

    1 for every district in a municipality / province / national scope; for a WMA, the share of the district's area
    inside the WMA (approximate: volumes are assumed spread evenly over the district).
    """
    if scope.level != "wma":
        return dict.fromkeys(scope.district_codes, 1.0)
    ov, areas = spatial.wma_overlap(), spatial.district_areas()
    if ov is None or areas is None:
        raise DataUnavailable("WMA aggregation needs district boundaries", missing=["district boundaries"])
    return {code: a / areas[code] for code, a in ov.get(scope.id, {}).items()}


def class_areas(classes: pd.Series, w: dict[str, float]) -> dict[str, float]:
    """Σ weight (area) per class label for a district-indexed Series of class ids."""
    out: dict[str, float] = {}
    for code, cls in classes.items():
        if code in w and isinstance(cls, str):
            out[cls] = out.get(cls, 0.0) + w[str(code)]
    return out


def aggregation_label(scope: ScopeSel) -> str:
    return {"municipality": "district", "wma": "district-overlap"}.get(scope.level, "district-area-weighted")


def weighted(df: pd.DataFrame, w: dict[str, float], by: str | list[str] = "date", value: str = "value") -> pd.DataFrame:
    """Area-weighted mean of `value` per `by`. Returns columns [*by, value, coverage]."""
    keys = [by] if isinstance(by, str) else list(by)
    sub = df[df["district"].isin(w.keys()) & df[value].notna()].copy()
    total_w = sum(w.values())
    if sub.empty or total_w <= 0:  # keep the key dtypes (e.g. datetime `date`) so callers can use .dt on it
        empty = sub[keys].iloc[0:0].copy() if set(keys) <= set(sub.columns) else pd.DataFrame(columns=keys)
        empty[value] = pd.Series(dtype=float)
        empty["coverage"] = pd.Series(dtype=float)
        return empty.reset_index(drop=True)
    sub["_w"] = sub["district"].map(w)
    sub["_wx"] = sub["_w"] * sub[value]
    g = sub.groupby(keys, as_index=False).agg(_wx=("_wx", "sum"), _w=("_w", "sum"))
    g[value] = g["_wx"] / g["_w"]
    g["coverage"] = g["_w"] / total_w
    g.loc[g["coverage"] < MIN_COVERAGE, value] = float("nan")
    return g[[*keys, value, "coverage"]]


def weighted_scalar(per_district: pd.Series, w: dict[str, float]) -> float | None:
    """Area-weighted mean of a district-indexed Series; None below the coverage threshold."""
    s = per_district[per_district.index.isin(w.keys())].dropna()
    total_w = sum(w.values())
    if s.empty or total_w <= 0:
        return None
    ws = pd.Series({k: w[k] for k in s.index})
    if ws.sum() / total_w < MIN_COVERAGE:
        return None
    return float((s * ws).sum() / ws.sum())


# ---------------------------------------------------------------- normals


def monthly_normals(total: pd.DataFrame, anomaly_mm: pd.DataFrame) -> pd.DataFrame:
    """Per district and calendar month: normal = value − stored anomaly (the DB's 2000–2026 climatology).

    The stored normal is constant per district-month (verified), so the median over years is exact.
    Columns: district, month (1–12), normal.
    """
    m = total.merge(anomaly_mm, on=["district", "date"], suffixes=("", "_a"))
    m["normal"] = m["value"] - m["value_a"]
    m["month"] = m["date"].dt.month
    return m.groupby(["district", "month"], as_index=False)["normal"].median()


def window_normal(normals: pd.DataFrame, start: dt.date, end: dt.date) -> pd.Series:
    """Normal rainfall per district over [start, end], pro-rating months by the days inside the window."""
    days = pd.date_range(start, end, freq="D")
    frac = (
        pd.DataFrame({"month": days.month, "dim": [calendar.monthrange(d.year, d.month)[1] for d in days]})
        .assign(f=lambda x: 1.0 / x["dim"])
        .groupby("month")["f"]
        .sum()
    )
    n = normals.assign(f=normals["month"].map(frac).fillna(0.0))
    n["part"] = n["normal"] * n["f"]
    return n.groupby("district")["part"].sum()


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def is_complete_month(month: dt.date, newest_daily: dt.date) -> bool:
    """True when the month ends on or before the newest daily record."""
    last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    return last <= newest_daily


def period_gap(label: str, first: int, last: int, data_first: int | None, data_last: int | None) -> str | None:
    """Why a statistic over the years [first, last] cannot be computed from data covering [data_first, data_last], or
    None when the data cover the whole period. A period the data only partly cover is not computed: averaging the
    covered years would present, say, one year (2000) as a 1981–2000 climatology.
    """
    if data_first is None or data_last is None:
        return f"No data for this scope in {label}"
    if data_first > first:
        return f"The database starts in {data_first}; the {label} period needs data from {first}"
    if data_last < last:
        return f"The database ends in {data_last}; the {label} period needs data to {last}"
    return None


def short_of_coverage(present: set[str], groups: dict[str, dict[str, float]]) -> list[str]:
    """Names of the groups (provinces, WMAs) whose districts with data cover < 95 % of their weight."""
    out = []
    for name, w in groups.items():
        total = sum(w.values())
        if total <= 0 or sum(v for k, v in w.items() if k in present) < MIN_COVERAGE * total:
            out.append(name)
    return out


def coverage_reason(what: str, short: list[str], of: int) -> str:
    """Reason for a by-province / by-WMA value that is null because of the 95 % area-coverage rule."""
    shown = ", ".join(short[:5]) + (f" and {len(short) - 5} more" if len(short) > 5 else "")
    return (
        f"Districts with {what} cover less than {MIN_COVERAGE:.0%} of the area of {len(short)} of {of} "
        f"({shown}); an area-weighted value needs {MIN_COVERAGE:.0%}"
    )
