"""Database view: every table is a category, every column a variable (docs/CATEGORY_CATALOGUE.md).

    GET /api/v1/{module}/categories                        the module's tables, chart groups and columns
    GET /api/v1/{module}/category/{tableId}/{groupId}      a SeriesSet: one series per requested column (`vars`)

Values are read straight from `table.column` (no recomputation). Scope aggregation follows the other endpoints:
area-weighted district mean with ≥ 95 % area coverage for intensive quantities (mm, °C, %, indices), and a sum over
the scope's districts for volumes, flows and areas (Mm³, m³/s, m³/day, m³/year, km²; WMA: the district's share of area
inside the WMA). `entityId` selects one district (`Ctx.for_entity`). Monthly and annual values the database repeats on
every daily row are read once per period; incomplete months / years (relative to the newest record in the database)
are never served; daily series longer than `MAX_SERIES_POINTS` are downsampled to monthly means. Withheld columns are
all-null series with an `unavailable` entry and the reason. One axis per group: the catalogue never mixes units.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Path, Query, Request

from app.auth import get_current_user
from app.core import districts
from app.core.aggregate import MIN_COVERAGE, sum_fractions
from app.core.errors import BadRequest, NotFound
from app.core.filters import ScopeSel, parse_scope
from app.core.palettes import SERIES
from app.core.provenance import Resolution, iso, latest_complete_period_end, period_end
from app.core.stats import rnd
from app.db import ident
from app.modules.common import Ctx, cached, get_ctx
from app.registry.catalogue import Catalogue, CatColumn, Category, CatGroup, get_catalogue
from app.registry.registry import Dataset
from app.schemas import contract as c
from app.state import AppState

log = logging.getLogger("niwis.categories")
HISTORY_START = dt.date(2000, 1, 1)
EXTENSIVE_UNITS = {"Mm³", "Mm³/month", "m³/s", "m³/day", "m³/year", "km²"}  # summed over districts, not averaged
DECIMALS = {"°C": 2, "%": 1, "% FSC": 1, "index": 2, "—": 3, "m³/m³": 3, "°C·days": 1, "km²": 1}
OPTS: dict[str, Any] = {"response_model_exclude_unset": True, "response_model_by_alias": True}
ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": c.ErrorBody, "description": "Unknown column in `vars` (lists the valid ones) or bad filter"},
    404: {"model": c.ErrorBody, "description": "Unknown table or group for this module"},
}


def _decimals(unit: str | None) -> int:
    return DECIMALS.get(unit or "—", 2)


def _value_sql(ds: Dataset, column: str) -> str:
    """The column as double precision (text-stored numbers cast as the registry says; a long-format table's variable
    read from its value column)."""
    expr = ds.raw_sql(column)
    col = ds.columns.get(column)
    if col is not None and col.cast == "double":
        expr = f"NULLIF({expr}, '')"
    elif col is not None and col.cast == "bool_pct":
        expr = f"CASE WHEN {expr} THEN 100.0 WHEN NOT {expr} THEN 0.0 END"
    return ds.in_range(column, f"({expr})::double precision")


def _raw_period_sql(ds: Dataset) -> str:
    """The raw date expression `max()` can run on without casting every row (ISO text sorts as dates)."""
    t = ds.time
    if t.kind in ("date", "text_date"):
        assert t.column
        return ident(t.column)
    if t.kind == "year_month":
        assert t.year and t.month
        return f"make_date({ident(t.year)}::int, {ident(t.month)}::int, 1)"
    assert t.column
    return ident(t.column)


def _to_date(ds: Dataset, v: Any) -> dt.date | None:
    if v is None:
        return None
    if ds.time.kind == "year":
        return dt.date(int(v), 1, 1)
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def _span(col: Any) -> str:
    """The valid range of a registry column, e.g. "May 2002 – Aug 2024", "from Mar 2015"."""
    if col.valid_from and col.valid_until:
        return f"{col.valid_from:%b %Y} – {col.valid_until:%b %Y}"
    return f"from {col.valid_from:%b %Y}" if col.valid_from else f"up to {col.valid_until:%b %Y}"


def _period_start(d: dt.date, res: Resolution) -> dt.date:
    if res == "monthly":
        return d.replace(day=1)
    if res == "annual":
        return dt.date(d.year, 1, 1)
    return d


class CategoryService:
    def __init__(self, ctx: Ctx, module: str) -> None:
        self.ctx = ctx.for_entity()
        self.module = module
        self.state: AppState = ctx.state
        self.catalogue: Catalogue = get_catalogue()
        self.module_newest: dict[int, dt.date] = {}  # newest daily record per db module (complete_cap); see refs()

    async def refs(self) -> None:
        """Load the per-module completeness reference dates (cached by the meta service)."""
        if not self.module_newest and self.state.db.configured:
            self.module_newest = await self.state.meta.newest_by_module()

    # ---------------------------------------------------------------- lookups

    def category(self, table_id: str) -> Category:
        mine = self.catalogue.for_module(self.module)
        cat = next((x for x in mine if x.id == table_id), None)
        if cat is None:
            raise NotFound(
                f"Unknown table {table_id!r} for module {self.module}",
                issues=[{"param": "tableId", "allowed": [x.id for x in mine]}],
            )
        return cat

    def view(self, cat: Category) -> list[CatColumn]:
        """The table's columns as this page shows them: a duplicate whose canonical table is not on this page is a
        live column here, noted "Same values as table.column"."""
        shown = set(cat.page(self.module).shown_duplicates)
        out = []
        for x in cat.columns:
            if x.column in shown:
                note = f"Same values as {x.duplicate_of}" + (f"; {x.note}" if x.note else "")
                x = x.model_copy(update={"status": "live", "note": note})
            out.append(x)
        return out

    def group(self, cat: Category, group_id: str) -> CatGroup:
        g = cat.group(group_id, self.module)
        if g is None:
            allowed = [x.id for x in cat.groups_for(self.module)]
            raise NotFound(f"Unknown group {group_id!r} in {cat.id}", issues=[{"param": "groupId", "allowed": allowed}])
        return g

    def dataset(self, cat: Category) -> Dataset:
        return self.state.registry.datasets[cat.table]

    def selected(self, cat: Category, g: CatGroup) -> list[CatColumn]:
        raw = (self.ctx.params.get("vars") or "").strip()
        if not raw:
            names = list(g.default_visible)
        elif raw.lower() == "all":
            names = list(g.columns)
        else:
            names = list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))
            bad = [x for x in names if x not in g.columns]
            if bad or not names:
                raise BadRequest(
                    f"Unknown column(s) {bad or [raw]} for group {g.id} of {cat.id}",
                    issues=[{"param": "vars", "invalid": bad, "allowed": [*g.columns, "all"]}],
                )
        cols = {x.column: x for x in self.view(cat)}
        return [cols[n] for n in names]

    # ---------------------------------------------------------------- dates

    async def column_dates(self, cat: Category) -> dict[str, dt.date | None]:
        """Newest raw period with a value per served column (live, or a duplicate a page may show), plus `""` = the
        table's newest record (one scan, cached)."""
        ds = self.dataset(cat)
        live = [x for x in cat.columns if x.status != "withheld"]
        p = _raw_period_sql(ds)
        exprs = [
            f"{_value_sql(ds, x.column) if x.kind == 'numeric' else ds.raw_sql(x.column)} IS NOT NULL "
            f"AND {ds.valid_sql(x.column)}"
            for x in live
        ]
        filtered = (f"max({p}) FILTER (WHERE {e}) AS c{i}" for i, e in enumerate(exprs))
        parts = [f"max({p}) AS c_all", *filtered]
        sql = f"SELECT {', '.join(parts)} FROM {ds.fq} WHERE {ds.rows_sql()}"  # rows after today never count

        async def load() -> dict[str, dt.date | None]:
            row = await self.state.db.fetchrow(sql)
            out: dict[str, dt.date | None] = {"": _to_date(ds, row["c_all"]) if row else None}
            for i, x in enumerate(live):
                out[x.column] = _to_date(ds, row[f"c{i}"]) if row else None
            return out

        try:
            res: dict[str, dt.date | None] = await self.state.cache.get_or_set(
                f"cat:dates:{cat.table}", self.state.settings.cache_ttl_meta_s, load
            )
            return res
        except Exception:  # e.g. a statement timeout on a large unindexed table: fall back, do not cache
            log.warning("category dates failed; using the table's newest date", extra={"table": cat.table})
            items = (await self.state.meta.sources())["items"]
            newest = next((it.get("asOf") for it in items if it.get("table") == cat.table), None)
            d = dt.date.fromisoformat(newest) if newest else None
            return {"": d, **{x.column: d for x in live}}

    def complete_cap(
        self, res: Resolution, ds: Dataset | None = None, table_newest: dt.date | None = None
    ) -> dt.date | None:
        """Start of the latest complete period (None: no cap).

        Complete relative to the newest daily record of the table's database module (6.2's months are complete only
        when 6.1, the module's daily table, has their last day), or the newest daily record in the database for modules
        without a daily table; a daily table is also capped at its own newest day (`refs()` must have run)."""
        newest = self.ctx.newest_db
        if ds is not None and ds.db_module in self.module_newest:
            mod = self.module_newest[ds.db_module]
            newest = min(newest, mod) if newest is not None else mod
        if ds is not None and ds.time.resolution == "daily" and table_newest is not None:
            newest = min(newest, table_newest) if newest is not None else table_newest
        if newest is None or res == "daily":
            return newest
        return _period_start(latest_complete_period_end(newest, res), res)

    def as_of(self, x: CatColumn, dates: dict[str, dt.date | None], ds: Dataset | None = None) -> dt.date | None:
        d = dates.get(x.column)
        if d is None:
            return None
        d = _period_start(d, x.resolution)
        cap = self.complete_cap(x.resolution, ds, dates.get(""))
        return min(d, cap) if cap is not None else d

    # ---------------------------------------------------------------- /categories

    async def categories(self) -> dict[str, Any]:
        await self.refs()
        tables, newest_all, sources = [], [], []
        for cat in self.catalogue.for_module(self.module):
            dates = await self.column_dates(cat)
            ds = self.dataset(cat)
            variables = []
            cols = self.view(cat)
            for x in cols:
                g = cat.group_of(self.module, x.column)
                a = self.as_of(x, dates, ds) if x.status == "live" else None
                extra: dict[str, Any] = {}
                if a is not None:
                    extra = {"as_of": iso(a), **self.ctx.stamp(a, x.resolution)}
                variables.append(
                    c.CatalogueVariable(
                        column=x.column,
                        label=x.label,
                        name=x.name,
                        **({"unit": x.unit} if x.unit else {}),
                        kind=x.kind,
                        resolution=x.resolution,
                        status=x.status,
                        **({"reason": x.reason} if x.reason else {}),
                        **({"duplicate_of": x.duplicate_of} if x.duplicate_of else {}),
                        **({"group": g.id} if g else {}),
                        default_visible=bool(g and x.column in g.default_visible),
                        chart=x.chart,
                        source=f"{cat.table}.{x.column}",
                        **({"note": x.note} if x.note else {}),
                        **({"unit_override": x.unit_override} if x.unit_override else {}),
                        **extra,
                    )
                )
            live_dates = [(d, x.resolution) for x in cols if x.status == "live" and (d := self.as_of(x, dates, ds))]
            t_as_of, t_res = max(live_dates) if live_dates else (None, "daily")
            res_t: Resolution = "monthly" if t_res == "monthly" else "annual" if t_res == "annual" else "daily"
            counts = {s: sum(1 for x in cols if x.status == s) for s in ("live", "withheld", "duplicate")}
            tables.append(
                c.CatalogueTable(
                    id=cat.id,
                    table=cat.table,
                    label=cat.label,
                    **({"spec": cat.spec, "spec_tag": f"§{cat.spec}"} if cat.spec else {}),
                    modules=list(cat.modules),
                    resolution=cat.resolution,
                    **({"as_of": iso(t_as_of), "is_stale": self.ctx.stale(t_as_of, res_t)} if t_as_of else {}),
                    counts=counts,
                    groups=[
                        c.CatalogueGroup(
                            id=g.id,
                            label=g.label,
                            unit=g.unit,
                            resolution=g.resolution,
                            columns=list(g.columns),
                            default_visible=list(g.default_visible),
                            path=f"/api/v1/{self.module}/category/{cat.id}/{g.id}",
                            chart=g.chart,
                            **({"band": list(g.band)} if g.band else {}),
                        )
                        for g in cat.groups_for(self.module)
                    ],
                    variables=variables,
                )
            )
            if t_as_of:
                newest_all.append(t_as_of)
                sources.append(cat.table)
        as_of = max(newest_all) if newest_all else None
        body = c.CategoryList(
            module=self.module,
            as_of=iso(as_of),
            tables=tables,
            source=sorted(set(sources)),
            aggregation=self.ctx.aggregation,
        )
        return body.dump()

    # ---------------------------------------------------------------- /category/{table}/{group}

    def window(self, g: CatGroup, newest: dt.date, ds: Dataset | None = None) -> tuple[dt.date, dt.date]:
        """[start, end] for the group: the page period anchored at the table's newest record; annual groups show the
        whole record unless `from` / `to` or a 5Y / 10Y period narrow it (the climate pages' convention)."""
        win = self.ctx.filters.window
        if g.resolution == "annual":
            cap_y = self.complete_cap("annual", ds, newest)
            last = min(newest.year, cap_y.year) if cap_y is not None else newest.year
            if win.date_from or win.date_to:
                y0 = win.date_from.year if win.date_from else HISTORY_START.year
                y1 = min(win.date_to.year, last) if win.date_to else last
            elif win.period in ("5Y", "10Y"):
                y1, y0 = last, last - int(win.period[:-1]) + 1
            else:
                y0, y1 = HISTORY_START.year, last
            return dt.date(y0, 1, 1), dt.date(y1, 12, 31)
        if g.resolution == "monthly":  # whole months, ending with the latest complete month the table has
            cap = self.complete_cap("monthly", ds, newest)
            last_month = min(newest.replace(day=1), cap) if cap is not None else newest.replace(day=1)
            start, end = win.resolve(period_end(last_month, "monthly"), HISTORY_START)
            return start.replace(day=1), end
        return win.resolve(newest, HISTORY_START)

    def factors(self, extensive: bool) -> tuple[dict[str, float], float]:
        """District → weight (area for means, share of the district for sums), and the total weight."""
        if extensive:
            f = sum_fractions(self.ctx.filters.scope, self.state.spatial)
            return f, float(len(f))
        w = self.ctx.weights()
        return w, float(sum(w.values()))

    @staticmethod
    def served(g: CatGroup, cols: list[CatColumn]) -> list[CatColumn]:
        """The group's numeric columns that hold data (live, or a duplicate shown on some page): read in one scan."""
        by = {x.column: x for x in cols}
        return [by[k] for k in g.columns if by[k].status != "withheld" and by[k].kind == "numeric"]

    async def scope_group(
        self, ds: Dataset, g: CatGroup, cols: list[CatColumn], start: dt.date, end: dt.date, downsample: bool
    ) -> dict[str, pd.Series]:
        """Scope value per period (index: period start) of every served numeric column of a group, from ONE scan of
        the table (a daily table is ~500 000 rows and unindexed; one scan per column made daily groups slow,
        BACKEND_ISSUES #13). Cached per group, scope and window; requests pick their `vars` from it."""
        loc, d = ident(ds.location.column), ds.date_sql()
        if g.resolution == "daily" and downsample:
            period, inner = f"date_trunc('month', {d})::date", "avg"
        elif g.resolution == "daily":
            period, inner = d, "max"
        else:
            trunc = "month" if g.resolution == "monthly" else "year"
            period, inner = f"date_trunc('{trunc}', {d})::date", "max"  # one value per period (repeats)
        if not cols:
            return {}
        extensive = (cols[0].unit or "") in EXTENSIVE_UNITS  # one unit per group (axis rule)
        factors, total = self.factors(extensive)
        names: list[str] = []
        facs: list[float] = []
        for code, f in factors.items():
            for n in await self.state.data.names(ds, code):
                names.append(n)
                facs.append(float(f))
        inner_cols = ", ".join(
            f"{inner}({_value_sql(ds, x.column)}) FILTER (WHERE {ds.valid_sql(x.column)}) AS v{i}"
            for i, x in enumerate(cols)
        )
        outer_cols = ", ".join(
            f"sum(d.v{i} * w.f) AS num{i}, sum(w.f) FILTER (WHERE d.v{i} IS NOT NULL) AS den{i}, count(d.v{i}) AS n{i}"
            for i in range(len(cols))
        )
        # Monthly / annual values repeated on every row of a daily table: read only the first day of each period (the
        # value is the same on every day — checked for every repeated column, and the first day always exists). With
        # the (district, date) index that is ~12 / 26 rows per district instead of every daily row: the scoped
        # seasonal totals read 156 rows instead of 57 000 (10 484 cold pages, 6.7 s; BACKEND_ISSUES #14).
        first_days: list[Any] = []
        if ds.time.resolution == "daily" and g.resolution in ("monthly", "annual") and ds.repeats_on_first_day:
            freq = "MS" if g.resolution == "monthly" else "YS"
            days = [t.date() for t in pd.date_range(_period_start(start, g.resolution), end, freq=freq)]
            first_days = [x.isoformat() for x in days] if ds.time.kind == "text_date" else days
        one_day = (
            f" AND {ident(str(ds.time.column))} = ANY($5::{'text' if ds.time.kind == 'text_date' else 'date'}[])"
            if first_days
            else ""
        )
        sql = (
            f"WITH d AS (SELECT {loc} AS name, {period} AS p, {inner_cols} "
            f"FROM {ds.fq} WHERE {ds.range_sql()}{one_day} AND {loc} = ANY($3::text[]) GROUP BY 1, 2) "
            f"SELECT d.p, {outer_cols} "
            "FROM d JOIN unnest($3::text[], $4::float8[]) AS w(name, f) ON w.name = d.name GROUP BY 1 ORDER BY 1"
        )
        colkey = ",".join(x.column for x in cols)
        key = f"cat:group:{ds.table}:{colkey}:{start}:{end}:{downsample}:{hash(tuple(sorted(factors.items())))}"

        async def load() -> dict[str, pd.Series]:
            args = [*ds.range_args(start, end), names, facs, *([first_days] if first_days else [])]
            rows = await self.state.db.fetch(sql, *args)
            out: dict[str, pd.Series] = {}
            for i, x in enumerate(cols):
                vals: dict[pd.Timestamp, float] = {}
                for r in rows:
                    num, den, n = r[f"num{i}"], r[f"den{i}"], r[f"n{i}"]
                    if num is None or not den:
                        continue
                    if extensive:
                        if n == len(factors):  # a sum needs every district of the scope
                            vals[pd.Timestamp(r["p"])] = float(num)
                    elif float(den) >= MIN_COVERAGE * total:
                        vals[pd.Timestamp(r["p"])] = float(num) / float(den)
                idx = pd.DatetimeIndex(list(vals.keys()))
                out[x.column] = pd.Series(list(vals.values()), index=idx, dtype=float).sort_index()
            return out

        res: dict[str, pd.Series] = await self.state.cache.get_or_set(
            key, self.state.settings.cache_ttl_long_series_s, load
        )
        return res

    async def district_categories(self, ds: Dataset, x: CatColumn, start: dt.date, end: dt.date) -> pd.Series:
        """A categorical column for one district: its value per period (the latest row of the period)."""
        loc, d = ident(ds.location.column), ds.date_sql()
        trunc = {"daily": None, "monthly": "month", "annual": "year"}[x.resolution]
        period = f"date_trunc('{trunc}', {d})::date" if trunc else d
        dist = districts.by_code(self.ctx.filters.scope.id)
        assert dist is not None
        names = await self.state.data.names(ds, dist.code)
        sql = (
            f"SELECT DISTINCT ON (p) {period} AS p, {ds.raw_sql(x.column)} AS v FROM {ds.fq} "
            f"WHERE {ds.range_sql()} AND {ds.valid_sql(x.column)} AND {loc} = ANY($3::text[]) "
            f"AND {ds.raw_sql(x.column)} IS NOT NULL ORDER BY p, {d} DESC"
        )

        async def load() -> pd.Series:
            rows = await self.state.db.fetch(sql, *ds.range_args(start, end), names)
            return pd.Series([r["v"] for r in rows], index=pd.DatetimeIndex([r["p"] for r in rows]), dtype=object)

        key = f"cat:class:{ds.table}:{x.column}:{dist.code}:{start}:{end}"
        res: pd.Series = await self.state.cache.get_or_set(key, self.state.settings.cache_ttl_long_series_s, load)
        return res

    @staticmethod
    def grid(res: Resolution, start: dt.date, end: dt.date, downsample: bool) -> list[pd.Timestamp]:
        if res == "annual":
            return [pd.Timestamp(y, 1, 1) for y in range(start.year, end.year + 1)]
        if res == "monthly" or downsample:
            return list(pd.date_range(start.replace(day=1), end.replace(day=1), freq="MS"))
        return list(pd.date_range(start, end, freq="D"))

    async def series(self, table_id: str, group_id: str) -> dict[str, Any]:
        await self.refs()
        cat = self.category(table_id)
        g = self.group(cat, group_id)
        cols = self.selected(cat, g)
        ds = self.dataset(cat)
        dates = await self.column_dates(cat)
        newest = dates.get("") or self.ctx.newest_db or dt.date.today()
        start, end = self.window(g, newest, ds)
        downsample = g.resolution == "daily" and (end - start).days + 1 > self.state.settings.max_series_points
        cap = self.complete_cap(g.resolution, ds, newest)
        single = self.ctx.filters.scope.level == "municipality"
        values: dict[str, pd.Series] = {}
        unavailable: list[c.UnavailableField] = []
        group_data = await self.scope_group(ds, g, self.served(g, self.view(cat)), start, end, downsample)
        for x in cols:
            if x.status != "live":
                why = x.reason if x.status == "withheld" else f"duplicate of {x.duplicate_of}"
                unavailable.append(c.UnavailableField(field=f"series.{x.column}", reason=f"{x.label}: {why}"))
                continue
            if x.kind == "categorical":
                if not single:
                    unavailable.append(
                        c.UnavailableField(
                            field=f"series.{x.column}",
                            reason=f"{x.label} is a class per district: select one district (entityId or municipality)",
                        )
                    )
                    continue
                s = await self.district_categories(ds, x, start, end)
            else:
                s = group_data[x.column]
            if g.resolution != "daily" and cap is not None:
                s = s[s.index <= pd.Timestamp(cap)]  # incomplete months / years are never served
            values[x.column] = s
            rule = (
                ""
                if single
                else " (a sum needs every district of the scope)"
                if (x.unit or "") in EXTENSIVE_UNITS
                else f" (an area-weighted value needs districts covering ≥ {MIN_COVERAGE:.0%} of the area)"
            )
            reg = ds.columns.get(x.column)
            if reg is not None and reg.valid_until and start > period_end(reg.valid_until, "monthly"):
                self.ctx.explain(  # the selected period lies after the column's last valid month
                    f"series.{x.column}",
                    f"{x.label}: served up to {reg.valid_until:%b %Y} only ({reg.valid_reason}); choose a period "
                    f"ending by {reg.valid_until:%b %Y}",
                )
            self.ctx.explain(f"series.{x.column}", f"No {x.label} for this scope in the selected period{rule}")
        xs = sorted({t for s in values.values() for t in s.index}) or [
            t for t in self.grid(g.resolution, start, end, downsample) if cap is None or t <= pd.Timestamp(cap)
        ]
        nd = _decimals(g.unit)
        series: list[c.Series] = []
        as_ofs: list[dt.date] = []
        for i, x in enumerate(cols):
            s = values.get(x.column)
            if s is None:
                data: list[list[Any]] = [[iso(t.date()), None] for t in xs]
            elif x.kind == "categorical":
                data = [[iso(t.date()), s.get(t)] for t in xs]
            else:
                data = [[iso(t.date()), rnd(s.get(t), nd)] for t in xs]
            have = [t for t, v in (s.items() if s is not None else []) if v is not None and not pd.isna(v)]
            a = max(have).date() if have else None
            extra: dict[str, Any] = {}
            if a is not None:
                res: Resolution = "monthly" if downsample else x.resolution
                extra = {"as_of": iso(a), **self.ctx.stamp(a, res)}
                as_ofs.append(a)
            series.append(
                c.Series(
                    key=x.column,
                    name=x.label,
                    kind="bar" if x.chart in ("bar", "diverging-bar") else "line",
                    chart=x.chart,
                    axis=0,
                    color=SERIES[i % len(SERIES)],
                    data=data,
                    **({"unit": x.unit} if x.unit else {}),
                    source=[f"{cat.table}.{x.column}"],
                    resolution=x.resolution,
                    status=x.status,
                    **extra,
                )
            )
        as_of = max(as_ofs) if as_ofs else None
        res_top: Resolution = "monthly" if downsample else g.resolution
        prov = self.ctx.provenance(as_of, [cat.table], res=res_top)
        prov.pop("asOf")
        if any("anomaly" in x.column.lower() for x in cols if x.status == "live"):
            prov["baseline"] = self.state.registry.baseline.stored_anomalies
            prov["baselineNote"] = self.state.registry.baseline.note
        how = (
            "district value"
            if single
            else "sum over the scope's districts"
            if (g.unit in EXTENSIVE_UNITS)
            else f"area-weighted mean of district values (≥ {MIN_COVERAGE:.0%} of the area)"
        )
        limited = [
            f"{x.label}: served {_span(reg)} only ({str(reg.valid_reason).split(';')[0]})."
            for x in cols
            if (reg := ds.columns.get(x.column)) is not None and (reg.valid_from or reg.valid_until)
        ]
        note = (
            f"Values as stored in {cat.table} ({how}). "
            + ("Daily values downsampled to monthly means. " if downsample else "")
            + ("Incomplete periods are not shown. " if g.resolution != "daily" else "")
            + " ".join(limited)
        ).strip()
        body = c.SeriesSet(
            id=f"{cat.id}/{g.id}",
            title=f"{g.label} — {cat.label}",
            x_type="time",
            axes=[c.Axis(unit=g.unit, label=g.label)],
            series=series,
            chart=g.chart,
            **({"band": list(g.band)} if g.band else {}),
            as_of=iso(as_of),
            note=note,
            **({"unavailable": unavailable} if unavailable else {}),
            **prov,
        )
        out = body.dump()
        if not as_ofs:
            out["isStale"] = False
        return out


def build_category_router(module: str) -> APIRouter:
    router = APIRouter(prefix=f"/api/v1/{module}", tags=[module], dependencies=[Depends(get_current_user)])

    @router.get(
        "/categories",
        response_model=c.CategoryList,
        responses=ERRORS,
        summary="Database tables (categories), their chart groups and every column",
        **OPTS,
    )
    async def categories(request: Request, ctx: Ctx = Depends(get_ctx)) -> Any:
        ttl = ctx.state.settings.cache_ttl_summary_s
        return await cached(request, ctx, ttl, lambda: CategoryService(ctx, module).categories())

    @router.get(
        "/category/{table_id}/{group_id}",
        response_model=c.SeriesSet,
        responses=ERRORS,
        summary="One chart group of a database table: a series per column",
        **OPTS,
    )
    async def category_series(
        request: Request,
        table_id: str = Path(..., description="Category id from /categories, e.g. core-rainfall-variables"),
        group_id: str = Path(..., description="Chart group id, e.g. seasonal-total-mm"),
        vars: str | None = Query(None, description="Comma-separated column names; `all` for every column"),
        ctx: Ctx = Depends(get_ctx),
    ) -> Any:
        ttl = ctx.state.settings.cache_ttl_long_series_s
        return await cached(request, ctx, ttl, lambda: CategoryService(ctx, module).series(table_id, group_id))

    return router


def prewarm_scopes(state: AppState) -> list[ScopeSel]:
    """The scopes to pre-warm (`PREWARM_SCOPES`): "national" or "province=WC", "municipality=DC32", "wma=WMA5"."""
    out = []
    for item in state.settings.prewarm_scopes:
        key, _, value = item.partition("=")
        if key == "national":
            out.append(parse_scope())
        elif key in ("province", "municipality", "wma") and value:
            out.append(parse_scope(**{key: value}, wma_districts=state.spatial.wma_districts()))
        else:
            log.warning("ignoring unknown pre-warm scope", extra={"scope": item})
    return out


async def prewarm(state: AppState) -> int:
    """Load what every category chart needs for its first request (period 12M) in each pre-warm scope — national
    and the scopes the frontend uses (BACKEND_ISSUES #13, #14): the newest date per column of every table, then every
    chart group of every page, so they answer from cache. At most `db_pool_max − 3` queries run at once
    (`db.background`), so requests always find a connection. Returns the number of (scope, group) entries warmed; a
    failure is logged and skipped."""
    from app.core.filters import Filters, Window

    newest = await state.meta.newest_date()
    sem = state.db.background  # shared with the table statistics: requests always find a free connection
    cats = get_catalogue().categories
    scopes = prewarm_scopes(state)
    national = Ctx(state, Filters(parse_scope(), Window("12M", None, None), False, None), newest)

    async def dates(cat: Category) -> None:
        async with sem:
            await CategoryService(national, cat.modules[0]).column_dates(cat)  # never raises (falls back)

    await asyncio.gather(*(dates(cat) for cat in cats))
    jobs: dict[tuple[str, str, str], tuple[CategoryService, Category, CatGroup]] = {}
    for scope in scopes:
        ctx = Ctx(state, Filters(scope, Window("12M", None, None), False, None), newest)
        for cat in cats:
            for module in cat.modules:
                svc = CategoryService(ctx, module)
                for g in cat.groups_for(module):
                    cols = svc.served(g, svc.view(cat))
                    classes = [
                        x
                        for x in svc.view(cat)
                        if x.column in g.columns and x.kind == "categorical" and x.status == "live"
                    ]
                    if cols or (classes and scope.level == "municipality"):  # same table + columns: one cache entry
                        key = (f"{scope.level}:{scope.id}", cat.table, ",".join(x.column for x in cols))
                        jobs.setdefault(key, (svc, cat, g))

    async def one(svc: CategoryService, cat: Category, g: CatGroup) -> bool:
        async with sem:
            try:
                await svc.refs()
                dts = await svc.column_dates(cat)
                start, end = svc.window(g, dts.get("") or newest or dt.date.today(), svc.dataset(cat))
                down = g.resolution == "daily" and (end - start).days + 1 > state.settings.max_series_points
                await svc.scope_group(svc.dataset(cat), g, svc.served(g, svc.view(cat)), start, end, down)
                if svc.ctx.filters.scope.level == "municipality":  # class columns are served for one district
                    for x in svc.view(cat):
                        if x.column in g.columns and x.kind == "categorical" and x.status == "live":
                            await svc.district_categories(svc.dataset(cat), x, start, end)
                return True
            except Exception:
                log.exception("category pre-warm failed", extra={"table": cat.table, "group": g.id})
                return False

    done = await asyncio.gather(*(one(*job) for job in jobs.values()))
    log.info(
        "category groups warmed",
        extra={"groups": sum(done), "of": len(done), "scopes": [f"{s.level}:{s.id}" for s in scopes]},
    )
    return sum(done)


async def keep_warm(state: AppState) -> None:
    """Warm every category group now, then again shortly before the cached series expire."""
    while True:
        try:
            await prewarm(state)
        except Exception:
            log.exception("category pre-warm round failed")
        # after 80 % of the TTL: the reads then trigger background reloads (refresh-ahead) before anything expires
        await asyncio.sleep(max(300, int(state.settings.cache_ttl_long_series_s * 0.8)))
