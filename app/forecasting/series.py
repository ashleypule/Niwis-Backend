"""The series a forecast is made from, read from the database (read-only) the way the dashboard reads it.

Target = a live numeric catalogue column (table + column), or one of a few derived page variables (pages.py: runoff
depth, hot days, % of area in D1–D4) defined by an SQL expression over a registered column.

Per district and month:
- a column that holds one value per month (monthly tables, monthly values repeated on daily rows): that value;
- a daily column: the month's **sum** for amounts (`aggregate: sum` in the catalogue: rainfall, evaporation, runoff,
  day counts, daily volumes), needing every day of the month but one (the database's own monthly totals miss 31 Dec),
  or the month's **mean** for states (temperatures, levels, flows, indices), needing one value.
Then over the scope's districts, as on the dashboard: an area-weighted mean with ≥ 95 % of the area reporting, or a
sum over every district for volumes and flows (WMA: each district's share of area). Only rows the dashboard serves are
read: never after today, inside the column's `valid_from` / `valid_until`, impossible values dropped, incomplete months
never used (the latest complete month of the table's database module, D5).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from app.core import districts
from app.core.aggregate import MIN_COVERAGE, sum_fractions
from app.core.errors import BadRequest, NotFound
from app.core.filters import Filters, ScopeSel, Window
from app.db import ident
from app.forecasting.base import Freq
from app.modules.categories import EXTENSIVE_UNITS, CategoryService, _value_sql
from app.modules.common import Ctx
from app.registry.catalogue import Aggregate, CatColumn, Category, get_catalogue, monthly_unit
from app.state import AppState

FORECASTABLE: tuple[str, ...] = ("daily", "monthly")  # annual columns hold ≤ 27 values: too short to forecast
NONNEG_UNITS_EXCLUDED = {None, "°C", "m amsl", "m", "%/year", "—", "index"}
CLASS_WORDS = ("spi", "spei")


@dataclass(frozen=True)
class Target:
    table: str
    column: str
    label: str
    unit: str | None  # unit of the series as forecast (a daily amount summed per month: per month)
    resolution: Literal["daily", "monthly"]  # the column's own resolution
    aggregate: Aggregate
    extensive: bool  # summed over the scope's districts (volumes, flows) instead of area-weighted
    expr: str | None = None  # SQL value expression (derived page variables); default: the stored column
    expr_id: str = ""  # cache key part for `expr`
    scale: float = 1.0  # unit conversion applied to the scope value (9_1 m³/day → m³/s)
    classify: bool = False  # SPI / SPEI: D0–D4 classes and probabilities
    anomaly: bool = False
    note: str | None = None
    sources: tuple[str, ...] = field(default=())
    native_unit: str | None = None  # unit of the stored daily value (daily forecasts)

    @property
    def source(self) -> list[str]:
        return list(self.sources) or [f"{self.table}.{self.column}"]

    def resolutions(self) -> list[Freq]:
        return ["monthly", "daily"] if self.resolution == "daily" else ["monthly"]

    def unit_for(self, freq: Freq) -> str | None:
        return self.unit if freq == "monthly" else (self.native_unit or self.unit)

    def bounds(self, history_min: float) -> tuple[float | None, float | None]:
        """Physical limits for the forecast: never below 0 for quantities that cannot be negative (when the history
        never is), never above 100 for % shares; anomalies and indices are not bounded."""
        if self.anomaly or self.classify:
            return None, None
        unit = self.unit
        low = 0.0 if history_min >= 0 and unit not in NONNEG_UNITS_EXCLUDED else None
        high = 100.0 if unit in ("%", "% FSC") else None
        return low, high


def _tokens(column: str) -> set[str]:
    return {x.lower() for x in re.split(r"[_\s]+", column) if x}


def _column(table: str, column: str) -> tuple[Category, CatColumn]:
    cat = next((c for c in get_catalogue().categories if c.table == table), None)
    if cat is None:
        raise NotFound(f"Unknown table {table!r}", issues=[{"param": "table"}])
    col = cat.column(column)
    if col is None:
        raise NotFound(f"Unknown column {column!r} in {table}", issues=[{"param": "column"}])
    return cat, col


def why_not_forecastable(col: CatColumn) -> str | None:
    if col.kind != "numeric":
        return f"{col.label} is a class, not a number"
    if col.status == "withheld":
        return f"{col.label} is withheld: {col.reason}"
    if col.status == "duplicate":
        return f"{col.label} is a duplicate of {col.duplicate_of}: forecast that column instead"
    if col.resolution not in FORECASTABLE:
        return f"{col.label} is {col.resolution}: too few values (one a year) to forecast"
    return None


def target_from_catalogue(table: str, column: str) -> Target:
    _cat, col = _column(table, column)
    why = why_not_forecastable(col)
    if why:
        raise BadRequest(why, issues=[{"param": "column", "value": column, "reason": why}])
    agg: Aggregate = col.aggregate or "mean"
    tokens = _tokens(column)
    res: Literal["daily", "monthly"] = "daily" if col.resolution == "daily" else "monthly"
    return Target(
        table=table,
        column=column,
        label=col.name,
        unit=monthly_unit(col.unit, agg, col.resolution),
        resolution=res,
        aggregate=agg,
        extensive=(col.unit or "") in EXTENSIVE_UNITS,
        classify=bool(tokens & set(CLASS_WORDS)),
        anomaly="anomaly" in tokens,
        note=col.note,
        native_unit=col.unit,
    )


def module_of(table: str) -> str:
    cat = next(c for c in get_catalogue().categories if c.table == table)
    return cat.modules[0]


def context(state: AppState, scope: ScopeSel, newest: dt.date | None) -> Ctx:
    return Ctx(state, Filters(scope, Window("historical", None, None), False, None), newest)


@dataclass
class SeriesData:
    values: pd.Series  # regular index (month starts / days), NaN where the scope has no value
    as_of: dt.date | None  # last period with a value
    first: dt.date | None
    unit: str | None
    note: str


async def build_series(state: AppState, target: Target, scope: ScopeSel, freq: Freq) -> SeriesData:
    """The scope's series of `target` at `freq` (cached per target, scope, resolution and newest database date)."""
    if freq not in target.resolutions():
        raise BadRequest(
            f"{target.label} is monthly: daily forecasts need a daily column", issues=[{"param": "resolution"}]
        )
    newest = await state.meta.newest_date() if state.db.configured else None
    key = f"fc:series:{target.table}:{target.column}:{target.expr_id}:{scope.level}:{scope.id}:{freq}:{newest}"

    async def load() -> SeriesData:
        return await _load(state, target, scope, freq, newest)

    res: SeriesData = await state.cache.get_or_set(key, state.settings.cache_ttl_long_series_s, load)
    return res


async def _load(state: AppState, target: Target, scope: ScopeSel, freq: Freq, newest: dt.date | None) -> SeriesData:
    ds = state.registry.datasets[target.table]
    ctx = context(state, scope, newest)
    svc = CategoryService(ctx, module_of(target.table))
    await svc.refs()
    loc, d = ident(ds.location.column), ds.date_sql()
    value = target.expr or _value_sql(ds, target.column)
    valid = ds.valid_sql(target.column) if target.column in ds.columns else ds.rows_sql()
    if freq == "daily":
        period, inner = d, "avg"
    elif target.resolution == "monthly":
        period, inner = f"date_trunc('month', {d})::date", "avg"  # one value per month (repeats: the same each day)
    else:
        period, inner = f"date_trunc('month', {d})::date", ("sum" if target.aggregate == "sum" else "avg")
    extensive = target.extensive
    factors = sum_fractions(scope, state.spatial) if extensive else ctx.weights()
    by_name: dict[str, str] = {}
    for code in factors:
        for n in await state.data.names(ds, code):
            by_name[n] = code
    sql = (
        f"SELECT {loc} AS name, {period} AS p, {inner}({value}) FILTER (WHERE {valid}) AS v, "
        f"count({value}) FILTER (WHERE {valid}) AS n "
        f"FROM {ds.fq} WHERE {ds.rows_sql()} AND {loc} = ANY($1::text[]) GROUP BY 1, 2"
    )
    async with state.db.background:
        rows = await state.db.fetch_slow(sql, sorted(by_name))
    frame = pd.DataFrame(
        [(by_name[r["name"]], pd.Timestamp(r["p"]), r["v"], r["n"]) for r in rows if r["v"] is not None],
        columns=["district", "p", "v", "n"],
    )
    if freq == "monthly" and target.resolution == "daily" and target.aggregate == "sum" and not frame.empty:
        days = frame["p"].map(lambda t: calendar.monthrange(t.year, t.month)[1])
        frame = frame[frame["n"] >= days - 1]  # a month's total needs every day but one (31 Dec is often missing)
    per = frame.groupby(["p", "district"])["v"].mean().unstack("district") if not frame.empty else pd.DataFrame()
    values = _combine(per, factors, extensive) * target.scale
    # incomplete periods are never used: the latest complete month of the table's database module (D5)
    dates = await svc.column_dates(next(c for c in get_catalogue().categories if c.table == target.table))
    table_newest = dates.get("")
    cap = svc.complete_cap("daily" if freq == "daily" else "monthly", ds, table_newest)
    if cap is not None:
        values = values[values.index <= pd.Timestamp(cap)]
    values = values.dropna()
    if values.empty:
        return SeriesData(pd.Series(dtype=float), None, None, target.unit_for(freq), "")
    idx = pd.date_range(values.index.min(), values.index.max(), freq="MS" if freq == "monthly" else "D")
    values = values.reindex(idx)
    how = (
        "district value"
        if scope.level == "municipality"
        else "sum over the scope's districts"
        if extensive
        else f"area-weighted mean of district values (≥ {MIN_COVERAGE:.0%} of the area)"
    )
    temporal = (
        "daily values"
        if freq == "daily"
        else "one value per month"
        if target.resolution == "monthly"
        else "monthly totals of daily values"
        if target.aggregate == "sum"
        else "monthly means of daily values"
    )
    note = f"{', '.join(target.source)}: {temporal}, {how}."
    return SeriesData(
        values, values.last_valid_index().date(), values.first_valid_index().date(), target.unit_for(freq), note
    )


def _combine(per: pd.DataFrame, factors: dict[str, float], extensive: bool) -> pd.Series:
    """Scope value per period from district values (columns): a sum needs every district; a mean ≥ 95 % of the
    weight."""
    if per.empty:
        return pd.Series(dtype=float)
    cols = [c for c in per.columns if c in factors]
    w = pd.Series({c: factors[c] for c in cols}, dtype=float)
    data = per[cols]
    have = data.notna()
    if extensive:
        ok = have.sum(axis=1) == len(factors)
        out = (data.fillna(0) * w).sum(axis=1)
        return out.where(ok).astype(float)
    total = float(sum(factors.values()))
    covered = (have * w).sum(axis=1)
    out = (data.fillna(0) * w).sum(axis=1) / covered.replace(0, float("nan"))
    return out.where(covered >= MIN_COVERAGE * total).astype(float)


def scope_label(scope: ScopeSel) -> dict[str, Any]:
    level = "district" if scope.level == "municipality" else scope.level
    return {"level": level, "id": scope.id, "name": scope.name}


def district_scope(code: str) -> ScopeSel:
    d = districts.by_code(code)
    if d is None:
        raise NotFound(f"Unknown district {code!r}", issues=[{"param": "municipality"}])
    return ScopeSel("municipality", d.code, d.official_name, (d.code,))
