"""Groundwater Level Monitoring (contract module `groundwater`, spec §10; DB module 8: `10_1`, `10_2`).

`10_1_borehole_and_aquifer_master` holds one record per district (no borehole ids or coordinates; DECISIONS D12):

- depth to water (m bgl) from NGA telemetry on irregular days (about every seven weeks, the same days in every
  district): a month's value is the mean of its samples, months without a sample are gaps; comparisons use 12-month
  means (last 12 months vs the 12 before) so they never depend on which months were sampled;
- level anomaly = latest sampled month − the mean of all samples in that calendar month over the full years
  2000–(last full year), sign flipped so + = shallower (higher water table), as the contract's chart says;
- SGI from `hydrological_drought_master` (the 10_1 SGI is withheld, D8), valid May 2002 – Aug 2024 only, so it is
  served marked stale and the SGI KPI / borehole status classes stay null with the reason;
- recharge from `11.2` direct recharge (mm/year; × district area for Mm³/year), abstraction from `11.3` total
  abstraction (m³/year, summed); `10_1`'s recharge rate (41 districts share a series) and aquifer constants are
  withheld (D9); the per-borehole abstraction / pumping rate and recharge fraction are shown on the entity card;
- `10_2` recharge-supporting variables in the database view.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from app.core import districts
from app.core.palettes import NORMAL, SERIES, YEAR_COLOURS, drought_bands
from app.core.provenance import iso
from app.core.stats import rnd, sens_slope, trend_line
from app.modules.common import Ctx, anomaly_split_reasons
from app.modules.district import NO_POINTS, DistrictModule
from app.modules.rainfall.service import MONTHS
from app.schemas import contract as c

MODULE = "groundwater"
DEPTH = "bh_groundwater_table_depth_m_bgl"
HEAD = "bh_hydraulic_head_amsl_m"
HEAD_T = "bh_hydraulic_head_m_amsl"
RECHARGE = "rd_direct_recharge"  # 11.2, mm/year
ABSTRACTION = "as_total_abstraction"  # 11.3, m³/year
SGI = "sgi"
HISTORY_START = dt.date(2000, 1, 1)
DEPTH_CLASSES = ((None, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 100), (100, None))  # gwDepth palette
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
ENTITY_DATASETS: set[tuple[str, str]] = set()
DEPTH_NOTE = (
    "Depth to water from NGA telemetry (10_1), one record per district; a month's value is the mean of its samples "
    "(about one every seven weeks), unsampled months are gaps."
)


def depth_class(v: float) -> str:
    for lo, hi in DEPTH_CLASSES:
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return f"< {hi}" if lo is None else f"> {lo}" if hi is None else f"{lo}–{hi}"
    raise AssertionError("unreachable")


class GroundwaterService(DistrictModule):
    MODULE = MODULE

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    # ---------------------------------------------------------------- data

    async def depth_frame(self) -> pd.DataFrame:
        df = await self.monthly_frame(DEPTH)
        return df[df["date"] <= await self.latest_month(DEPTH)]

    async def last_full_year(self) -> int:
        last = await self.latest_month(DEPTH)
        return int(last.year) if last.month == 12 else int(last.year) - 1

    async def twelve_month_change(self) -> tuple[pd.Series, pd.Timestamp]:
        """Per district: mean depth of the last 12 months − mean of the 12 before (m; negative = water rising)."""
        df = await self.depth_frame()
        last = await self.latest_month(DEPTH)
        a = last - pd.DateOffset(months=12)
        b = last - pd.DateOffset(months=24)
        cur = df[(df["date"] > a) & (df["date"] <= last)].groupby("district")["value"].mean()
        prev = df[(df["date"] > b) & (df["date"] <= a)].groupby("district")["value"].mean()
        return (cur - prev).dropna(), last

    async def anomaly(self, df: pd.DataFrame | None = None) -> tuple[pd.Series, pd.Timestamp, int]:
        """Per district: latest sampled month vs the same-calendar-month mean of the full years (+ = shallower)."""
        df = await self.depth_frame() if df is None else df
        last = await self.latest_month(DEPTH)
        y1 = await self.last_full_year()
        ref = df[(df["date"].dt.year >= HISTORY_START.year) & (df["date"].dt.year <= y1)]
        normal = ref[ref["date"].dt.month == last.month].groupby("district")["value"].mean()
        cur = df[df["date"] == last].set_index("district")["value"]
        return (normal - cur).dropna(), last, y1

    async def scope_depth(self) -> pd.Series:
        return await self.scope_series(DEPTH)

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        last = await self.latest_month(DEPTH)
        depth = await self.scope_value(DEPTH, last)
        s = await self.scope_depth()
        an, _, y1 = await self.anomaly()
        anomaly = self.aggregate(an, False)
        kpis = [
            self.kpi_unavailable("boreholes", "boreholes", "Monitoring Boreholes", "boreholes", NO_POINTS),
            self.kpi(
                "level",
                "gwLevel",
                "Mean Groundwater Level",
                depth,
                "m bgl",
                last,
                DEPTH,
                decimals=1,
                sublabel=f"depth to water, {last:%b %Y}" + ("" if len(self.ctx.weights()) == 1 else " (area-weighted)"),
                spark=[v for v in (rnd(x, 1) for x in s.tail(12)) if v is not None],
            ),
            self.kpi(
                "anomaly",
                "gwLevelAnomaly",
                "Level Anomaly",
                anomaly,
                "m",
                last,
                DEPTH,
                decimals=2,
                sublabel=f"vs {last:%B} mean {HISTORY_START.year}–{y1}; + = shallower",
                good_direction="up",
            ),
        ]
        sgi_df = await self.data.monthly(SGI)
        sgi_last = pd.Timestamp(sgi_df["date"].max()) if not sgi_df.empty else None
        why = (
            f"Latest valid SGI is {sgi_last:%b %Y} (more than {self.ctx.state.settings.stale_after_days} days old): "
            + self.registry.datasets["hydrological_drought_master"]
            .columns["sgi_dimensionless"]
            .valid_reason.split(";")[0]  # type: ignore[union-attr]
            if sgi_last is not None
            else self.reason(SGI)
        ) + "; the 10_1 SGI is withheld (not an index of any water level in the database)"
        kpis.append(self.kpi_unavailable("sgi", "sgi", "SGI", None, why))
        rm = await self.latest_month(RECHARGE)
        kpis.append(
            self.kpi(
                "recharge",
                "recharge",
                "Estimated Recharge",
                await self.scope_value(RECHARGE, rm),
                "mm/yr",
                rm,
                RECHARGE,
                decimals=0,
                sublabel=f"direct recharge {rm:%Y} (11.2)"
                + ("" if len(self.ctx.weights()) == 1 else ", area-weighted"),
            )
        )
        am = await self.latest_month(ABSTRACTION)
        ab = await self.scope_value(ABSTRACTION, am)
        kpis.append(
            self.kpi(
                "abstraction",
                "abstraction",
                "Annual Abstraction",
                ab / 1e6 if ab is not None else None,
                "Mm³/yr",
                am,
                ABSTRACTION,
                decimals=1,
                sublabel=f"total abstraction {am:%Y} (11.3), sum over the scope's districts",
                reason="A sum needs every district of the scope",
            )
        )
        ch, _ = await self.twelve_month_change()
        mine = ch[ch.index.isin(self.ctx.filters.scope.district_codes)]
        kpis.append(
            self.kpi(
                "rising",
                "stationsHighFlow",
                "Districts with Rising Levels",
                float((mine < 0).sum()) if len(mine) else None,
                "districts",
                last,
                DEPTH,
                decimals=0,
                sublabel=f"of {len(mine)}: mean depth of the last 12 months shallower than the 12 before",
            )
        )
        kpis.append(
            self.kpi_unavailable(
                "risk",
                "boreholesAtRisk",
                "Boreholes at Risk",
                None,
                "Borehole status classes come from a current SGI, which the database does not have; " + NO_POINTS,
            )
        )
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        handlers = {
            "level": self.ts_level,
            "level-long": self.ts_level_long,
            "anomaly": self.ts_anomaly,
            "sgi": self.ts_sgi,
            "seasonal": self.ts_seasonal,
            "recharge-abstraction": self.ts_recharge_abstraction,
        }
        if metric in handlers:
            return await handlers[metric]()
        raise self.no_key(
            "timeseries",
            metric,
            list(handlers),
            {
                "quality": "No groundwater EC in the database: 11.3 'Water quality for use' has no stated parameter or "
                "unit (spec §11.3 lists EC, pH, nitrate and TDS together)"
            },
        )

    async def ts_level(self) -> dict[str, Any]:
        start, end = await self.window(DEPTH)
        s = await self.scope_depth()
        y1 = await self.last_full_year()
        win = s[(s.index >= start) & (s.index <= end)]
        prev = pd.Series({t: s.get(t - pd.DateOffset(years=1)) for t in win.index}, dtype=float)
        ref = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)]
        lt = ref.groupby(ref.index.month).mean()
        series = [
            self.series("level", "Water level", win, nd=2, i=0, source=self.ctx.col_source(DEPTH).get("source")),
            self.series("prev", "Previous year", prev, nd=2, i=2),
            c.Series(
                key="lt",
                name=f"Long-term mean ({HISTORY_START.year}–{y1})",
                kind="line",
                color=NORMAL,
                dash=True,
                data=[[iso(t.date()), rnd(lt.get(t.month), 2)] for t in win.index],
            ),
        ]
        return self.series_set(
            "level",
            "Depth to water",
            series,
            [c.Axis(unit="m bgl", label="Depth to water", inverse=True)],
            [DEPTH],
            end,
            note=DEPTH_NOTE,
            baseline=f"{HISTORY_START.year}–{y1} same-month mean",
        )

    async def ts_level_long(self) -> dict[str, Any]:
        s = await self.scope_depth()
        y1 = await self.last_full_year()
        annual = s.groupby(s.index.year).mean()
        full = annual[(annual.index >= HISTORY_START.year) & (annual.index <= y1)]
        t = sens_slope(full.tolist())
        line = trend_line(full.tolist()) if t else [None] * len(full)
        if t is None:
            self.ctx.explain("series.trend", "Too few complete years for a trend (8 needed)")
        last = await self.latest_month(DEPTH)
        idx = [pd.Timestamp(int(y), 1, 1) for y in full.index]
        return self.series_set(
            "level-long",
            "Depth to water — long term",
            [
                self.series(
                    "observed",
                    "Observed level (annual mean)",
                    pd.Series(full.values, index=idx),
                    nd=2,
                    i=0,
                    resolution="annual",
                ),
                c.Series(
                    key="lt",
                    name=f"Long-term mean ({HISTORY_START.year}–{y1})",
                    kind="line",
                    color=NORMAL,
                    dash=True,
                    data=[[iso(x.date()), rnd(full.mean(), 2)] for x in idx],
                    resolution="annual",
                ),
                c.Series(
                    key="trend",
                    name=f"Trend (Sen's slope{f', {t.slope * 10:+.2f} m/decade' if t else ''})",
                    kind="line",
                    color=SERIES[3],
                    data=[[iso(x.date()), rnd(v, 2)] for x, v in zip(idx, line, strict=True)],
                    resolution="annual",
                ),
            ],
            [c.Axis(unit="m bgl", label="Depth to water", inverse=True)],
            [DEPTH],
            last,
            note=DEPTH_NOTE + f" Annual means of the sampled months, full years {HISTORY_START.year}–{y1}.",
        )

    async def ts_anomaly(self) -> dict[str, Any]:
        start, end = await self.window(DEPTH)
        s = await self.scope_depth()
        y1 = await self.last_full_year()
        ref = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)]
        lt = ref.groupby(ref.index.month).mean()
        win = s[(s.index >= start) & (s.index <= end)]
        an = pd.Series({t: lt.get(t.month) - v for t, v in win.items()}, dtype=float)  # + = shallower
        anomaly_split_reasons(self.ctx, [rnd(v, 2) for v in an.tolist()], "level anomaly")
        return self.series_set(
            "anomaly",
            "Groundwater level anomaly",
            [
                self.series("pos", "Positive anomaly (higher level)", an.where(an >= 0), nd=2, kind="bar", i=0),
                self.series("neg", "Negative anomaly (lower level)", an.where(an < 0), nd=2, kind="bar", i=3),
            ],
            [c.Axis(unit="m", label="Anomaly (+ = shallower)")],
            [DEPTH],
            end,
            note=DEPTH_NOTE + f" Anomaly vs the {HISTORY_START.year}–{y1} same-month mean; + = water table higher.",
            baseline=f"{HISTORY_START.year}–{y1} same-month mean",
        )

    async def ts_sgi(self) -> dict[str, Any]:
        df = await self.data.monthly(SGI)
        if df.empty:
            raise self.no_key("timeseries", "sgi", [], {"sgi": self.reason(SGI)})
        g = self.aggregate_frame(df)
        last = pd.Timestamp(df["date"].max())
        start, end = await self.window(DEPTH)
        win = g[(g.index >= start) & (g.index <= end)]
        if win.dropna().empty:  # the page period lies after the last valid SGI (Aug 2024): show its last 12 months
            win = g[g.index > last - pd.DateOffset(months=12)]
        reason = self.registry.datasets["hydrological_drought_master"].columns["sgi_dimensionless"].valid_reason
        return self.series_set(
            "sgi",
            "Standardised Groundwater Index (SGI)",
            [self.series("sgi", "SGI", win, nd=2, i=0, source=self.ctx.col_source(SGI).get("source"))],
            [c.Axis(unit="—", label="SGI")],
            [SGI],
            last,
            note=f"SGI from hydrological_drought_master, served May 2002 – {last:%b %Y} only: {reason}",
            bands=[c.Band.model_validate(b) for b in drought_bands()],
        )

    def aggregate_frame(self, df: pd.DataFrame) -> pd.Series:
        from app.core.aggregate import weighted

        g = weighted(df.assign(date=df["date"].map(lambda t: pd.Timestamp(t.year, t.month, 1))), self.ctx.weights())
        return g.set_index("date")["value"].dropna().sort_index()

    async def ts_seasonal(self) -> dict[str, Any]:
        s = await self.scope_depth()
        last = await self.latest_month(DEPTH)
        series = []
        for i, yr in enumerate(range(last.year, last.year - 5, -1)):
            ys = s[s.index.year == yr]
            data = [[MONTHS[m - 1], rnd(ys.get(pd.Timestamp(yr, m, 1)), 2)] for m in range(1, 13)]
            self.ctx.explain(f"series.{yr}", f"No depth-to-water sample for this scope in {yr}")
            series.append(
                c.Series(
                    key=str(yr),
                    name=str(yr),
                    kind="line",
                    color=(YEAR_COLOURS + SERIES)[i],
                    data=data,
                    symbol=True,
                )
            )
        return self.series_set(
            "seasonal",
            "Depth to water by month",
            series,
            [c.Axis(unit="m bgl", label="Depth to water", inverse=True)],
            [DEPTH],
            last,
            x_type="category",
            note=DEPTH_NOTE,
        )

    async def ts_recharge_abstraction(self) -> dict[str, Any]:
        """Annual recharge volume (11.2 direct recharge × district area) and total abstraction (11.3), Mm³/year."""
        areas = self.ctx.state.spatial.district_areas() or {}
        w = self.scope_weights(True)
        rdf = await self.monthly_frame(RECHARGE)
        adf = await self.monthly_frame(ABSTRACTION)
        y_last = min((await self.latest_month(RECHARGE)).year, (await self.latest_month(ABSTRACTION)).year)

        def volume(df: pd.DataFrame, factor: Any) -> pd.Series:
            out = {}
            for y, g in df.groupby(df["date"].dt.year):
                vals = g.set_index("district")["value"]
                if int(y) <= y_last and set(w) <= set(vals.index):
                    out[int(y)] = sum(float(vals[k]) * f * factor(k) for k, f in w.items())
            return pd.Series(out, dtype=float)

        rech = volume(rdf, lambda k: areas.get(k, np.nan) / 1000.0)  # mm × km² / 1000 = Mm³
        abst = volume(adf, lambda k: 1e-6)
        years = sorted(set(rech.index) & set(abst.index))
        if not years:
            for k in ("recharge", "abstraction", "net"):
                self.ctx.explain(f"series.{k}", "A sum needs every district of the scope")
        series = [
            c.Series(
                key="recharge",
                name="Recharge",
                kind="bar",
                color=SERIES[0],
                data=[[str(y), rnd(rech.get(y), 1)] for y in years],
            ),
            c.Series(
                key="abstraction",
                name="Abstraction",
                kind="bar",
                color=SERIES[3],
                data=[[str(y), rnd(abst.get(y), 1)] for y in years],
            ),
            c.Series(
                key="net",
                name="Net balance",
                kind="line",
                color=NORMAL,
                data=[[str(y), rnd(rech.get(y) - abst.get(y), 1)] for y in years],
            ),
        ]
        last = pd.Timestamp(y_last, 1, 1)
        body = self.series_set(
            "recharge-abstraction",
            "Recharge and abstraction",
            series,
            [c.Axis(unit="Mm³/yr", label="Volume")],
            [RECHARGE, ABSTRACTION],
            last,
            x_type="category",
            note="Recharge = direct recharge (11.2, mm/year) × district area; abstraction = total abstraction (11.3, "
            "m³/year); both summed over the scope's districts (every district needed).",
        )
        return body

    async def forecast(self, metric: str) -> dict[str, Any]:
        if metric == "level":
            raise self.forecast_none(metric)
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric == "distribution":
            last = await self.latest_month(DEPTH)
            vals = await self.district_values(DEPTH, last)
            mine = vals[vals.index.isin(self.ctx.filters.scope.district_codes)].dropna()
            labels = [depth_class(lo if lo is not None else 0.0) for lo, _ in DEPTH_CLASSES]
            counts = [float(sum(1 for v in mine if depth_class(float(v)) == lb)) for lb in labels]
            prov = self.ctx.provenance(last.date(), self.src(DEPTH), res="monthly")
            prov.pop("asOf")
            prov["aggregation"] = "district"
            return c.CategorySet(
                id="distribution",
                title=f"Districts by depth to water (m bgl) – {last:%b %Y}",
                unit="districts",
                categories=[f"{lb} m" for lb in labels],
                series=[c.CategorySeries(key="districts", name="Districts", values=counts)],
                as_of=iso(last.date()),
                **prov,
            ).dump()
        raise self.no_key(
            "breakdown",
            metric,
            ["distribution"],
            {
                "status": "Borehole status classes come from a current SGI, which the database does not have (the "
                "valid SGI ends Aug 2024)",
                "by-aquifer": "No aquifer units or aquifer codes in the database (districts only)",
                "sector": "Abstraction by sector is not in the database",
            },
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric == "lowest":
            return await self.tbl_lowest(limit)
        if metric == "declining":
            return await self.tbl_declining(limit)
        raise self.no_key(
            "table",
            metric,
            ["lowest", "declining"],
            {
                "aquifers": "No aquifer units or aquifer codes in the database (districts only)",
                "nitrate": "No groundwater nitrate in the database (12.2 nitrate is not identified as groundwater)",
            },
        )

    def _rows_scope(self, s: pd.Series) -> pd.Series:
        sel = self.ctx.filters.scope
        return s if sel.level == "national" else s[s.index.isin(sel.district_codes)]

    async def tbl_lowest(self, limit: int | None) -> dict[str, Any]:
        last = await self.latest_month(DEPTH)
        vals = self._rows_scope((await self.district_values(DEPTH, last)).dropna()).sort_values(ascending=False)
        ch, _ = await self.twelve_month_change()
        rows = []
        for i, (code, v) in enumerate(vals.items(), 1):
            d = districts.by_code(str(code))
            if d is None:
                continue
            delta = ch.get(code)
            status = None if delta is None or pd.isna(delta) else ("Falling" if delta > 0 else "Rising")
            rows.append(
                {
                    "id": d.code,
                    "rank": i,
                    "borehole": d.official_name,
                    "municipality": d.province,
                    "level": rnd(float(v), 1),
                    "status": status,
                }
            )
        prov = self.ctx.provenance(last.date(), self.src(DEPTH), res="monthly")
        prov.pop("asOf")
        prov["aggregation"] = "district"
        return c.TableSet(
            id="lowest",
            title=f"Deepest water levels by district – {last:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="borehole", label="District", type="text"),
                c.Column(key="municipality", label="Prov.", type="text"),
                c.Column(key="level", label="Level", type="number", unit="m bgl", decimals=1, align="right"),
                c.Column(key="status", label="12-month change", type="text"),
            ],
            rows=rows[: limit or 10],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(last.date()),
            **prov,
        ).dump()

    async def tbl_declining(self, limit: int | None) -> dict[str, Any]:
        ch, last = await self.twelve_month_change()
        df = await self.depth_frame()
        y1 = await self.last_full_year()
        trends: dict[str, float | None] = {}
        for code, g in df.groupby("district"):
            ann = g.groupby(g["date"].dt.year)["value"].mean()
            ann = ann[(ann.index >= HISTORY_START.year) & (ann.index <= y1)]
            t = sens_slope(ann.tolist())
            trends[str(code)] = t.slope if t else None
        rows = []
        for i, (code, v) in enumerate(self._rows_scope(ch).sort_values(ascending=False).items(), 1):
            d = districts.by_code(str(code))
            if d is None:
                continue
            tr = trends.get(d.code)
            rows.append(
                {
                    "id": d.code,
                    "rank": i,
                    "borehole": d.official_name,
                    "province": d.province,
                    "change": rnd(-float(v), 2),
                    "trend": rnd(-tr, 3) if tr is not None else None,
                }
            )
        self.ctx.explain("rows.trend", "Too few complete years for a trend (8 needed)")
        prov = self.ctx.provenance(last.date(), self.src(DEPTH), res="monthly")
        prov.pop("asOf")
        prov["aggregation"] = "district"
        return c.TableSet(
            id="declining",
            title="Largest declines in water level (12-month means)",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="borehole", label="District", type="text"),
                c.Column(key="province", label="Prov.", type="text"),
                c.Column(
                    key="change",
                    label="12-month change (+ = higher)",
                    type="delta",
                    unit="m",
                    decimals=2,
                    align="right",
                ),
                c.Column(key="trend", label="Trend (+ = rising)", type="delta", unit="m/yr", decimals=3, align="right"),
            ],
            rows=rows[: limit or 10],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(last.date()),
            **prov,
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "gw-level":
            return await self.district_map(layer, "Depth to water", DEPTH, "gwDepth", nd=1)
        if layer == "level-change":
            ch, last = await self.twelve_month_change()
            return self._map(
                layer,
                "Level change, last 12 months vs the 12 before (m, + = higher)",
                -ch,
                last,
                "tempAnomaly",
                "m",
                2,
                DEPTH,
            )
        if layer == "sgi":
            df = await self.data.monthly(SGI)
            last = pd.Timestamp(df["date"].max())
            vals = df[df["date"] == df["date"].max()].set_index("district")["value"]
            return self._map(
                layer,
                f"Standardised Groundwater Index – {last:%b %Y} (latest valid month)",
                vals,
                last,
                "spi",
                "—",
                2,
                SGI,
            )
        raise self.no_key(
            "map",
            layer,
            ["gw-level", "level-change", "sgi"],
            {
                "status": "Borehole status classes come from a current SGI, which the database does not have; "
                + NO_POINTS,
                "ec": "No groundwater EC in the database",
            },
        )

    def _map(
        self, layer: str, title: str, vals: pd.Series, last: pd.Timestamp, scale: str, unit: str, nd: int, var: str
    ) -> dict[str, Any]:
        out = {str(k): rnd(float(v), nd) for k, v in self._rows_scope(vals.dropna()).items()}
        if not out:
            self.ctx.explain("values", "No district of this scope has a value")
        prov = self.ctx.provenance(last.date(), self.src(var), res="monthly")
        prov["aggregation"] = "district"
        return c.MapLayer(
            id=layer,
            title=title,
            kind="choropleth",
            level="municipality",
            scale=scale,
            unit=unit,
            date=iso(last.date()) or "",
            values=out,
            geometry="districts",
            **prov,
        ).dump()

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.district_entities("district")

    async def entity(self, code: str) -> dict[str, Any]:
        d = self.district(code)
        ch, last = await self.twelve_month_change()
        delta = ch.get(d.code)
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            await self.field("Depth to water", DEPTH, d.code, nd=1),
            c.EntityField(
                label="12-month change (+ = higher)",
                value=rnd(-float(delta), 2) if delta is not None and pd.notna(delta) else None,
                unit="m",
                as_of=iso(last.date()),
            ),
            await self.field("Hydraulic head (monthly)", HEAD, d.code, nd=1),
            await self.field("Hydraulic head (telemetry)", HEAD_T, d.code, nd=1),
            await self.field("Recharge fraction", "bh_recharge_fraction_percent", d.code, nd=1),
            await self.field("Pumping rate per borehole", "bh_borehole_pumping_rate_m3_h", d.code, nd=1),
            await self.field("Abstraction per borehole", "bh_annual_abstraction_volume_m3_yr", d.code, nd=0),
            await self.field("Direct recharge", RECHARGE, d.code, nd=0),
            await self.field("Vadose-zone soil moisture", "rs_vadose_zone_soil_moisture_m3_m3", d.code, nd=3),
        ]
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type="district",
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code} · district groundwater record (10_1)",
            title=d.official_name,
            fields=fields,
            image="borehole",
            as_of=iso(last.date()),
            source=self.src(DEPTH, HEAD, RECHARGE, "rs_vadose_zone_soil_moisture_m3_m3"),
        ).dump()


async def prewarm(data: Any) -> None:
    for var in (DEPTH, HEAD_T):
        await data.daily_monthly_stats(var, ())
    for var in (HEAD, "rs_vadose_zone_soil_moisture_m3_m3"):
        await data.monthly(var)
    for var in (RECHARGE, ABSTRACTION, "bh_recharge_fraction_percent"):
        await data.annual(var)
