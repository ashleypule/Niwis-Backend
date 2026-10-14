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


def aggregation_label(scope: ScopeSel) -> str:
    return {"municipality": "district", "wma": "district-overlap"}.get(scope.level, "district-area-weighted")


def weighted(df: pd.DataFrame, w: dict[str, float], by: str | list[str] = "date", value: str = "value") -> pd.DataFrame:
    """Area-weighted mean of `value` per `by`. Returns columns [*by, value, coverage]."""
    keys = [by] if isinstance(by, str) else list(by)
    sub = df[df["district"].isin(w.keys()) & df[value].notna()].copy()
    total_w = sum(w.values())
    if sub.empty or total_w <= 0:
        return pd.DataFrame(columns=[*keys, value, "coverage"])
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
