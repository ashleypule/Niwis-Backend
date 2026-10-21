"""Shared building blocks for the §8–§12 modules (dams, surface water, groundwater, groundwater reserve, water quality).

Every §8–§12 table is keyed by the 52 district municipalities (no stations, boreholes or coordinates; DECISIONS D12),
so these modules serve districts the way the runoff module does:

- one value per district and month from the variable's canonical column (`monthly_frame`): monthly columns as stored,
  annual ones once per year, daily ones as the month's mean (state variables) or sum (totals); identical duplicate rows
  are read once; impossible values (`valid_range`) and rows after today never arrive;
- the latest **complete** month (`latest_month`): complete relative to the newest daily record of the table's database
  module, or the database's newest daily record (DECISIONS D5);
- scope values (`scope_value`, `scope_series`): area-weighted mean over the scope's districts (≥ 95 % of the area), or
  a sum for volumes / flows (every district needed; WMA: the district's share of area inside the WMA);
- district choropleths (`district_map`), KPIs with provenance (`kpi`, `kpi_unavailable`), the 52 districts as entities.

Anything the database cannot serve raises `DataUnavailable` with a specific reason, never a placeholder number.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any, Literal

import pandas as pd

from app.core import districts
from app.core.aggregate import MIN_COVERAGE, sum_fractions, weighted, weighted_scalar
from app.core.data import DataAccess
from app.core.errors import DataUnavailable, NotFound
from app.core.palettes import SERIES
from app.core.provenance import Resolution, iso, latest_complete_period_end
from app.core.stats import rnd
from app.modules.common import Ctx, forecast_unavailable, unknown
from app.registry.registry import Dataset
from app.schemas import contract as c

How = Literal["mean", "sum"]
NO_POINTS = (
    "No coordinates in the database: every §8–§12 table is keyed by district municipality (no stations, boreholes, "
    "dams or sampling points with locations), so point layers cannot be drawn"
)


def month_start(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return pd.Timestamp(t.year, t.month, 1)


def unavailable(detail: str, *missing: str) -> DataUnavailable:
    return DataUnavailable(detail, missing=list(missing))


class DistrictModule:
    """Base for a district-keyed module service. Subclasses set `MODULE` and implement the contract keys."""

    MODULE = ""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.data: DataAccess = ctx.data
        self.registry = ctx.state.registry

    # ---------------------------------------------------------------- registry helpers

    def source(self, var: str) -> tuple[Dataset, str]:
        return self.data.source(var)  # raises DataUnavailable with the withheld reason

    def has(self, var: str) -> bool:
        return self.registry.canonical_source(var) is not None

    def reason(self, var: str) -> str:
        """Why a variable is not served: its withheld reason(s), or "not in the database"."""
        label = self.registry.variables[var].label if var in self.registry.variables else var
        reasons = self.registry.withheld_reasons(var)
        if not reasons:
            return f"{label} is not in the database"
        table, _, why = reasons[0].partition(": ")
        return f"{label} is withheld ({table.split('.')[0]}): {why}"

    def src(self, *variables: str) -> list[str]:
        return sorted({self.source(v)[0].table for v in variables if self.has(v)})

    def resolution(self, var: str) -> Resolution:
        ds, col = self.source(var)
        rep = ds.columns[col].repeats
        if rep == "annual" or ds.time.resolution == "annual":
            return "annual"
        if rep == "monthly" or ds.time.resolution == "monthly":
            return "monthly"
        return "daily"

    def unit(self, var: str) -> str:
        return self.registry.variables[var].unit

    # ---------------------------------------------------------------- data

    async def monthly_frame(self, var: str, how: How = "mean") -> pd.DataFrame:
        """district, date (month start), value — one row per district-month (annual variables: per year, dated
        1 Jan)."""
        res = self.resolution(var)
        if res == "annual":
            df = (await self.data.annual(var)).copy()
            df["date"] = df["date"].map(lambda t: pd.Timestamp(pd.Timestamp(t).year, 1, 1))
        elif res == "monthly":
            df = (await self.data.monthly(var)).copy()
            df["date"] = df["date"].map(month_start)
        else:
            st = await self.data.daily_monthly_stats(var, ())
            df = st[["district", "date"]].copy()
            df["value"] = st["sum"] if how == "sum" else st["sum"] / st["days"].where(st["days"] > 0)
        df = df.dropna(subset=["value"])
        return df.groupby(["district", "date"], as_index=False)["value"].max()

    async def reference_date(self, var: str) -> dt.date | None:
        """The date to which the variable's months can be complete (DECISIONS D5)."""
        ds, _ = self.source(var)
        by_mod = await self.ctx.state.meta.newest_by_module()
        newest = self.ctx.newest_db
        ref = by_mod.get(ds.db_module, newest)
        return min(ref, newest) if ref is not None and newest is not None else ref or newest

    async def latest_month(self, var: str, how: How = "mean") -> pd.Timestamp:
        """Latest complete month (or year, for annual variables) with data for `var`."""
        df = await self.monthly_frame(var, how)
        if df.empty:
            raise unavailable(f"{self.registry.variables[var].label}: no values in the database", var)
        last = pd.Timestamp(df["date"].max())
        ref = await self.reference_date(var)
        if ref is not None:
            res = self.resolution(var)
            if res == "annual":
                cap = pd.Timestamp(latest_complete_period_end(ref, "annual").year, 1, 1)
            else:
                cap = month_start(latest_complete_period_end(ref, "monthly"))
            last = min(last, cap)
        return last

    async def district_values(self, var: str, month: pd.Timestamp, how: How = "mean") -> pd.Series:
        df = await self.monthly_frame(var, how)
        return df[df["date"] == month].set_index("district")["value"]

    def extensive(self, var: str) -> bool:
        return self.unit(var) in {"m³/s", "m³/day", "m³/year", "Mm³", "Mm³/year", "km²", "L/s", "m³/month"}

    def scope_weights(self, extensive: bool) -> dict[str, float]:
        if extensive:
            return sum_fractions(self.ctx.filters.scope, self.ctx.state.spatial)
        return self.ctx.weights()

    def aggregate(self, per_district: pd.Series, extensive: bool) -> float | None:
        """Scope value of a district-indexed Series: area-weighted mean (≥ 95 % of the area) or a sum over every
        district of the scope (WMA: share of the district inside the WMA)."""
        w = self.scope_weights(extensive)
        if not extensive:
            return weighted_scalar(per_district, w)
        s = per_district.dropna()
        if not w or not set(w) <= set(s.index):
            return None
        return float(sum(s[k] * f for k, f in w.items()))

    async def scope_value(self, var: str, month: pd.Timestamp, how: How = "mean") -> float | None:
        return self.aggregate(await self.district_values(var, month, how), self.extensive(var))

    async def scope_series(
        self, var: str, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None, how: How = "mean"
    ) -> pd.Series:
        """Scope value per month (or year) between start and end (inclusive), complete periods only."""
        df = await self.monthly_frame(var, how)
        last = await self.latest_month(var, how)
        df = df[df["date"] <= last]
        if start is not None:
            df = df[df["date"] >= start]
        if end is not None:
            df = df[df["date"] <= end]
        if self.extensive(var):
            w = self.scope_weights(True)
            sub = df[df["district"].isin(w)]
            n = sub.groupby("date")["district"].nunique()
            sub = sub.assign(v=sub["value"] * sub["district"].map(w))
            tot = sub.groupby("date")["v"].sum()
            return tot[n == len(w)].sort_index() if w else tot.iloc[0:0]
        g = weighted(df, self.scope_weights(False))
        return g.set_index("date")["value"].dropna().sort_index()

    async def window(self, var: str, how: How = "mean") -> tuple[pd.Timestamp, pd.Timestamp]:
        """[first month, last month] of the page period anchored at the variable's latest complete month."""
        last = await self.latest_month(var, how)
        win = self.ctx.filters.window
        if win.date_from or win.date_to:
            start = month_start(win.date_from) if win.date_from else pd.Timestamp(2000, 1, 1)
            end = min(month_start(win.date_to), last) if win.date_to else last
            return start, end
        months = {"latest": 1, "1M": 1, "3M": 3, "6M": 6, "12M": 12, "24M": 24, "5Y": 60, "10Y": 120}
        n = months.get(win.period or "12M")
        if n is None:  # historical
            return pd.Timestamp(2000, 1, 1), last
        return last - pd.DateOffset(months=n - 1), last

    # ---------------------------------------------------------------- response builders

    def stamp(self, month: pd.Timestamp | dt.date | None, var: str) -> dict[str, Any]:
        d = pd.Timestamp(month).date() if month is not None else None
        return {"as_of": iso(d), "source": self.src(var), **self.ctx.stamp(d, self.resolution(var))}

    def kpi(
        self,
        kid: str,
        metric: str,
        label: str,
        value: float | None,
        unit: str | None,
        month: pd.Timestamp | None,
        var: str,
        *,
        decimals: int = 1,
        sublabel: str | None = None,
        reason: str | None = None,
        status: c.Status | None = None,
        **extra: Any,
    ) -> c.Kpi:
        if value is None:
            self.ctx.explain(f"kpis.{kid}", reason or f"No {label} for this scope in the latest complete period")
        return c.Kpi(
            id=kid,
            metric=metric,
            label=label,
            sublabel=sublabel,
            value=rnd(value, decimals),
            **({"unit": unit} if unit is not None else {}),
            decimals=decimals,
            **self.stamp(month, var),
            **({"status": status} if status is not None else {}),
            **extra,
        )

    def kpi_unavailable(self, kid: str, metric: str, label: str, unit: str | None, reason: str) -> c.Kpi:
        self.ctx.explain(f"kpis.{kid}", reason)
        units = {"unit": unit} if unit is not None else {}  # the contract's unit is a string or absent, never null
        return c.Kpi(id=kid, metric=metric, label=label, value=None, unavailable_reason=reason, **units)

    def kpi_set(self, kpis: list[c.Kpi]) -> dict[str, Any]:
        dates = [dt.date.fromisoformat(k.as_of) for k in kpis if k.as_of and k.value is not None]
        as_of = max(dates) if dates else None
        body = c.KpiSet(
            module=self.MODULE,
            as_of=iso(as_of) or iso(self.ctx.newest_db) or "",
            scope=self.ctx.scope,
            kpis=kpis,
            components=[
                c.Component(
                    key=k.id, as_of=k.as_of, source=k.source or [], is_stale=bool(k.is_stale), period_end=k.period_end
                )
                for k in kpis
                if k.as_of
            ],
            source=sorted({s for k in kpis for s in (k.source or [])}),
            is_stale=any(bool(k.is_stale) for k in kpis if k.value is not None),
            aggregation=self.ctx.aggregation,
        )
        return body.dump()

    def series(
        self, key: str, name: str, s: pd.Series, *, nd: int = 2, kind: str = "line", i: int = 0, **extra: Any
    ) -> c.Series:
        return c.Series(
            key=key,
            name=name,
            kind=kind,
            color=SERIES[i % len(SERIES)],
            data=[[iso(pd.Timestamp(t).date()), rnd(v, nd)] for t, v in s.items()],
            **extra,
        )

    def series_set(
        self,
        sid: str,
        title: str,
        series: list[c.Series],
        axes: list[c.Axis],
        variables: Iterable[str],
        as_of: pd.Timestamp | None,
        *,
        x_type: str = "time",
        note: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        variables = list(variables)
        res = self.resolution(variables[0]) if variables else "monthly"
        d = pd.Timestamp(as_of).date() if as_of is not None else None
        prov = self.ctx.provenance(d, self.src(*variables), res=res)
        prov.pop("asOf")
        body = c.SeriesSet(
            id=sid,
            title=title,
            x_type=x_type,
            axes=axes,
            series=series,
            as_of=iso(d),
            **({"note": note} if note else {}),
            **prov,
            **extra,
        )
        return body.dump()

    async def district_map(
        self,
        layer: str,
        title: str,
        var: str,
        scale: str,
        *,
        nd: int = 1,
        how: How = "mean",
        transform: Any = None,
        unit: str | None = None,
        legend: list[c.LegendClass] | None = None,
    ) -> dict[str, Any]:
        """Choropleth of the latest complete month's district values (`transform`: value → mapped value)."""
        month = await self.latest_month(var, how)
        vals = await self.district_values(var, month, how)
        sel = self.ctx.filters.scope
        keep = set(sel.district_codes) if sel.level != "national" else {d.code for d in districts.districts()}
        out: dict[str, Any] = {}
        for code, v in vals.items():
            if code in keep and pd.notna(v):
                out[str(code)] = rnd(float(transform(v)) if transform else float(v), nd)
        if not out:
            self.ctx.explain("values", f"No {self.registry.variables[var].label} for these districts in {month:%b %Y}")
        res = self.resolution(var)
        res = "monthly" if res == "daily" else res  # daily values are mapped as the month's mean / total
        prov = self.ctx.provenance(month.date(), self.src(var), res=res)
        prov["aggregation"] = "district"
        label = f"{month:%Y}" if res == "annual" else f"{month:%b %Y}"
        return c.MapLayer(
            id=layer,
            title=f"{title} – {label}",
            kind="choropleth",
            level="municipality",
            scale=scale,
            unit=unit if unit is not None else self.unit(var),
            date=iso(month.date()) or "",  # the period's start, as every monthly / annual map (periodEnd: its end)
            values=out,
            geometry="districts",
            **({"legend": legend} if legend else {}),
            **prov,
        ).dump()

    # ---------------------------------------------------------------- entities

    def district_entities(self, entity_type: str = "district") -> dict[str, Any]:
        sel = self.ctx.filters.scope
        items = [
            c.Entity(
                id=d.code,
                name=d.official_name,
                type=entity_type,
                province=d.province,
                sub=f"{districts.PROVINCES[d.province]} · {d.kind}",
            )
            for d in sorted(districts.districts(), key=lambda d: d.official_name)
            if sel.level == "national" or d.code in sel.district_codes
        ]
        return c.EntityList(type=entity_type, total=len(items), items=items).dump()

    @staticmethod
    def district(code: str) -> districts.District:
        d = districts.by_code(code)
        if d is None:
            raise NotFound(f"Unknown entity {code!r}", issues=[{"param": "id", "hint": "a district MDB code"}])
        return d

    async def field(self, label: str, var: str, code: str, *, nd: int = 1, how: How = "mean") -> c.EntityField:
        """An entity-card field: the district's value for the variable's latest complete month."""
        if not self.has(var):
            unit = self.unit(var) if var in self.registry.variables else None
            return c.EntityField(label=label, value=None, unit=unit)
        month = await self.latest_month(var, how)
        v = (await self.district_values(var, month, how)).get(code)
        unit = self.unit(var)
        res = self.resolution(var)
        if how == "sum" and res == "daily" and unit.endswith("/day"):
            unit = unit.removesuffix("/day")  # a month's total of daily amounts (mm/day → mm)
        suffix = f"{month:%Y}" if res == "annual" else f"{month:%b %Y}"
        return c.EntityField(
            label=f"{label} ({suffix})",
            value=rnd(float(v), nd) if v is not None and pd.notna(v) else None,
            unit=None if unit == "—" else unit,
            as_of=iso(month.date()),
            is_stale=self.ctx.stale(month.date(), res),
        )

    # ---------------------------------------------------------------- unknown / unavailable keys

    def no_key(self, kind: str, key: str, served: Iterable[str], unavailable_keys: dict[str, str]) -> Exception:
        """501 with the specific reason for a contract key the database cannot serve; 404 for an unknown key."""
        if key in unavailable_keys:
            return unavailable(unavailable_keys[key], f"{kind}:{self.MODULE}:{key}")
        return unknown("metric" if kind != "map" else "layer", key, [*served, *unavailable_keys])

    def forecast_none(self, metric: str) -> Exception:
        return forecast_unavailable(self.MODULE, metric)


def coverage_note(extensive: bool) -> str:
    return (
        "sum over the scope's districts (every district needed)"
        if extensive
        else f"area-weighted mean of district values (≥ {MIN_COVERAGE:.0%} of the area)"
    )
