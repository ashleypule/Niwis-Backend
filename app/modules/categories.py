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

import datetime as dt
import logging
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Path, Query, Request

from app.auth import get_current_user
from app.core import districts
from app.core.aggregate import MIN_COVERAGE, sum_fractions
from app.core.errors import BadRequest, NotFound
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
    """The column as double precision (text-stored numbers cast as the registry says)."""
    expr = ident(column)
    col = ds.columns.get(column)
    if col is not None and col.cast == "double":
        expr = f"NULLIF({expr}, '')"
    return f"({expr})::double precision"


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

    def group(self, cat: Category, group_id: str) -> CatGroup:
        g = cat.group(group_id)
        if g is None:
            allowed = [x.id for x in cat.groups]
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
        cols = {x.column: x for x in cat.columns}
        return [cols[n] for n in names]

    # ---------------------------------------------------------------- dates

    async def column_dates(self, cat: Category) -> dict[str, dt.date | None]:
        """Newest raw period with a value, per live column, plus `""` = the table's newest record (one scan, cached)."""
        ds = self.dataset(cat)
        live = [x for x in cat.columns if x.status == "live"]
        p = _raw_period_sql(ds)
        exprs = [_value_sql(ds, x.column) if x.kind == "numeric" else ident(x.column) for x in live]
        filtered = (f"max({p}) FILTER (WHERE {e} IS NOT NULL) AS c{i}" for i, e in enumerate(exprs))
        parts = [f"max({p}) AS c_all", *filtered]
        sql = f"SELECT {', '.join(parts)} FROM {ds.fq}"

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

    def complete_cap(self, res: Resolution) -> dt.date | None:
        """Start of the latest complete period relative to the newest record in the database (None: no cap)."""
        newest = self.ctx.newest_db
        if newest is None or res == "daily":
            return newest
        return _period_start(latest_complete_period_end(newest, res), res)

    def as_of(self, x: CatColumn, dates: dict[str, dt.date | None]) -> dt.date | None:
        d = dates.get(x.column)
        if d is None:
            return None
        d = _period_start(d, x.resolution)
        cap = self.complete_cap(x.resolution)
        return min(d, cap) if cap is not None else d

    # ---------------------------------------------------------------- /categories

    async def categories(self) -> dict[str, Any]:
        tables, newest_all, sources = [], [], []
        for cat in self.catalogue.for_module(self.module):
            dates = await self.column_dates(cat)
            variables = []
            for x in cat.columns:
                a = self.as_of(x, dates) if x.status == "live" else None
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
                        **({"group": x.group} if x.group else {}),
                        default_visible=x.default_visible,
                        source=f"{cat.table}.{x.column}",
                        **({"note": x.note} if x.note else {}),
                        **({"unit_override": x.unit_override} if x.unit_override else {}),
                        **extra,
                    )
                )
            live_dates = [(d, x.resolution) for x in cat.columns if x.status == "live" and (d := self.as_of(x, dates))]
            t_as_of, t_res = max(live_dates) if live_dates else (None, "daily")
            res_t: Resolution = "monthly" if t_res == "monthly" else "annual" if t_res == "annual" else "daily"
            counts = {s: sum(1 for x in cat.columns if x.status == s) for s in ("live", "withheld", "duplicate")}
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
                        )
                        for g in cat.groups
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

    def window(self, g: CatGroup, newest: dt.date) -> tuple[dt.date, dt.date]:
        """[start, end] for the group: the page period anchored at the table's newest record; annual groups show the
        whole record unless `from` / `to` or a 5Y / 10Y period narrow it (the climate pages' convention)."""
        win = self.ctx.filters.window
        if g.resolution == "annual":
            cap_y = self.complete_cap("annual")
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
            cap = self.complete_cap("monthly")
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

    async def scope_series(
        self, ds: Dataset, x: CatColumn, start: dt.date, end: dt.date, downsample: bool
    ) -> pd.Series:
        """Scope value per period of one numeric column (index: period start), computed in SQL."""
        loc, d = ident(ds.location.column), ds.date_sql()
        if x.resolution == "daily" and downsample:
            period, inner = f"date_trunc('month', {d})::date", "avg"
        elif x.resolution == "daily":
            period, inner = d, "max"
        else:
            trunc = "month" if x.resolution == "monthly" else "year"
            period, inner = f"date_trunc('{trunc}', {d})::date", "max"  # one value per period (repeats)
        extensive = (x.unit or "") in EXTENSIVE_UNITS
        factors, total = self.factors(extensive)
        names: list[str] = []
        facs: list[float] = []
        for code, f in factors.items():
            dist = districts.by_code(code)
            if dist is not None:
                for n in {dist.db_name, dist.db_name.lower(), *dist.aliases}:
                    names.append(n)
                    facs.append(float(f))
        sql = (
            f"WITH d AS (SELECT {loc} AS name, {period} AS p, {inner}({_value_sql(ds, x.column)}) AS v "
            f"FROM {ds.fq} WHERE {ds.range_sql()} AND {loc} = ANY($3::text[]) GROUP BY 1, 2) "
            "SELECT d.p, sum(d.v * w.f) AS num, sum(w.f) FILTER (WHERE d.v IS NOT NULL) AS den, count(d.v) AS n "
            "FROM d JOIN unnest($3::text[], $4::float8[]) AS w(name, f) ON w.name = d.name GROUP BY 1 ORDER BY 1"
        )
        key = f"cat:series:{ds.table}:{x.column}:{start}:{end}:{downsample}:{hash(tuple(sorted(factors.items())))}"

        async def load() -> pd.Series:
            rows = await self.state.db.fetch(sql, *ds.range_args(start, end), names, facs)
            out: dict[pd.Timestamp, float] = {}
            for r in rows:
                if r["num"] is None or not r["den"]:
                    continue
                if extensive:
                    if r["n"] == len(factors):  # a sum needs every district of the scope
                        out[pd.Timestamp(r["p"])] = float(r["num"])
                elif float(r["den"]) >= MIN_COVERAGE * total:
                    out[pd.Timestamp(r["p"])] = float(r["num"]) / float(r["den"])
            return pd.Series(list(out.values()), index=pd.DatetimeIndex(list(out.keys())), dtype=float).sort_index()

        res: pd.Series = await self.state.cache.get_or_set(key, self.state.settings.cache_ttl_long_series_s, load)
        return res

    async def district_categories(self, ds: Dataset, x: CatColumn, start: dt.date, end: dt.date) -> pd.Series:
        """A categorical column for one district: its value per period (the latest row of the period)."""
        loc, d = ident(ds.location.column), ds.date_sql()
        trunc = {"daily": None, "monthly": "month", "annual": "year"}[x.resolution]
        period = f"date_trunc('{trunc}', {d})::date" if trunc else d
        dist = districts.by_code(self.ctx.filters.scope.id)
        assert dist is not None
        names = list({dist.db_name, dist.db_name.lower(), *dist.aliases})
        sql = (
            f"SELECT DISTINCT ON (p) {period} AS p, {ident(x.column)} AS v FROM {ds.fq} "
            f"WHERE {ds.range_sql()} AND {loc} = ANY($3::text[]) ORDER BY p, {d} DESC"
        )
        rows = await self.state.db.fetch(sql, *ds.range_args(start, end), names)
        return pd.Series([r["v"] for r in rows], index=pd.DatetimeIndex([r["p"] for r in rows]), dtype=object)

    @staticmethod
    def grid(res: Resolution, start: dt.date, end: dt.date, downsample: bool) -> list[pd.Timestamp]:
        if res == "annual":
            return [pd.Timestamp(y, 1, 1) for y in range(start.year, end.year + 1)]
        if res == "monthly" or downsample:
            return list(pd.date_range(start.replace(day=1), end.replace(day=1), freq="MS"))
        return list(pd.date_range(start, end, freq="D"))

    async def series(self, table_id: str, group_id: str) -> dict[str, Any]:
        cat = self.category(table_id)
        g = self.group(cat, group_id)
        cols = self.selected(cat, g)
        ds = self.dataset(cat)
        dates = await self.column_dates(cat)
        newest = dates.get("") or self.ctx.newest_db or dt.date.today()
        start, end = self.window(g, newest)
        downsample = g.resolution == "daily" and (end - start).days + 1 > self.state.settings.max_series_points
        cap = self.complete_cap(g.resolution)
        single = self.ctx.filters.scope.level == "municipality"
        values: dict[str, pd.Series] = {}
        unavailable: list[c.UnavailableField] = []
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
                s = await self.scope_series(ds, x, start, end, downsample)
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
                    kind="line",
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
        note = (
            f"Values as stored in {cat.table} ({how}). "
            + ("Daily values downsampled to monthly means. " if downsample else "")
            + ("Incomplete periods are not shown." if g.resolution != "daily" else "")
        ).strip()
        body = c.SeriesSet(
            id=f"{cat.id}/{g.id}",
            title=f"{g.label} — {cat.label}",
            x_type="time",
            axes=[c.Axis(unit=g.unit, label=g.label)],
            series=series,
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


async def prewarm(state: AppState) -> None:
    """Newest date per column of every table (one scan per table), so /categories is fast after a restart."""
    from app.core.filters import Filters, ScopeSel, Window

    scope = ScopeSel("national", "ZA", "South Africa", tuple(d.code for d in districts.districts()))
    ctx = Ctx(state, Filters(scope, Window("12M", None, None), False, None), await state.meta.newest_date())
    for cat in get_catalogue().categories:
        await CategoryService(ctx, cat.modules[0]).column_dates(cat)  # never raises (falls back)
