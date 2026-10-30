"""Surface Water Monitoring (contract module `surface-water`, spec §9; DB module 7: `9_1`, `9_2`, `9_3`).

`9_1_hydrological_station_master` holds one flow record per district (no stations, gauge codes or coordinates;
DECISIONS D12). Its daily flow family passes the checks and its monthly family does not (D10), so:

- discharge (m³/s) = daily mean flow (m³/day) / 86 400 s, a unit conversion of the stored value; a scope's flow is the
  sum over its districts (summed nationally the district flows give ≈ 40 700 Mm³/year, the order of the database's own
  MAR in 16_1), monthly flow = the mean of the month's daily flows;
- stage, flow velocity and suspended sediment concentration are monthly values (area-weighted for a scope);
- high / low flow: a district's latest complete month above its own 90th / below its 10th percentile of monthly mean
  flows over the full years 2000–(last full year); SSI-k = the standardised index (gamma per calendar month,
  app.core.indices) of the monthly mean flow — the spec's streamflow index computed from the stored discharge;
- `9_2` static catchment attributes on the entity card; `9_3` is withheld entirely (constancy check, DATA_GAPS §21);
- the modelled-runoff layers are the runoff module's (same source as the runoff page).

Not in the database (501 with the reason): gauging stations and point layers, baseflow separation, ecological-flow
requirements, abstraction licences, gauge quality, alerts (forecasts: app.forecasting.pages).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from app.core import districts
from app.core.indices import standardized_index
from app.core.palettes import NORMAL, OBSERVED, SERIES, YEAR_COLOURS
from app.core.provenance import iso
from app.core.stats import rnd, sens_slope
from app.core.thresholds import drought_class_or_none
from app.forecasting import pages as fc
from app.modules.common import Ctx, Selector, anomaly_split_reasons, drought_status
from app.modules.district import NO_POINTS, DistrictModule
from app.modules.rainfall.service import MONTHS
from app.schemas import contract as c

MODULE = "surface-water"
FLOW = "hs_daily_mean_flow"  # m³/day
STAGE = "hs_stage_height"
VELOCITY = "hs_flow_velocity"
SSC = "hs_suspended_sediment_concentration"
LOAD = "hs_daily_sediment_load_t_day"
HISTORY_START = dt.date(2000, 1, 1)
SECONDS = 86400.0
SEDIMENT_KIND = Selector("kind", ("conc", "load"), "conc")
TOP_ORDER = Selector("order", ("highest", "lowest"), "highest")
SELECTORS: dict[tuple[str, str], tuple[Selector, ...]] = {
    ("timeseries", "sediment"): (SEDIMENT_KIND,),
    ("table", "top-stations"): (TOP_ORDER,),
}
ENTITY_DATASETS = {
    ("timeseries", "discharge"),
    ("timeseries", "water-level"),
    ("timeseries", "sediment"),
    ("timeseries", "fdc"),
    ("timeseries", "seasonal"),
    ("timeseries", "flow-anomaly"),
    ("table", "statistics"),
}
STATUS = ("High Flow", "Normal", "Low Flow")
MAP_LAYERS = ("discharge", "flow-status", "flow-anomaly", "ssi")
RUNOFF_LAYERS = ("annual-runoff", "runoff-anomaly", "monthly-runoff", "runoff-coefficient")
FLOW_NOTE = (
    "Discharge = the stored daily mean flow (m³/day) / 86 400 s, summed over the scope's districts (9_1 holds one flow "
    "record per district; no gauging stations)."
)


class SurfaceWaterService(DistrictModule):
    MODULE = MODULE

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    def use_entity(self, kind: str, key: str) -> None:
        if (kind, key) in ENTITY_DATASETS and self.ctx.filters.entity_id:
            self.ctx = self.ctx.for_entity()

    def entity_title(self, title: str) -> str:
        return f"{title} — {self.ctx.filters.scope.name}" if self.ctx.filters.entity_id else title

    # ---------------------------------------------------------------- flows

    async def newest_day(self) -> dt.date:
        return await self.data.newest(FLOW)

    async def last_full_year(self) -> int:
        last = await self.latest_month(FLOW)
        return int(last.year) if last.month == 12 else int(last.year) - 1

    async def district_monthly_flow(self) -> pd.DataFrame:
        """district, date (month start), value = mean daily flow of the month in m³/s, complete months only."""
        df = await self.monthly_frame(FLOW)
        last = await self.latest_month(FLOW)
        df = df[df["date"] <= last].copy()
        df["value"] = df["value"] / SECONDS
        return df

    async def scope_monthly_flow(self) -> pd.Series:
        return (await self.scope_series(FLOW)) / SECONDS

    async def daily_flow(self, start: dt.date, end: dt.date) -> pd.Series:
        """Scope flow per day (m³/s): Σ district daily mean flow × share / 86 400, days with every district only."""
        s = await self.data.daily_scope_sum(FLOW, start, end, self.scope_weights(True))
        return s / SECONDS

    async def day_window(self) -> tuple[dt.date, dt.date]:
        newest = await self.newest_day()
        return self.ctx.filters.window.resolve(newest, HISTORY_START)

    async def thresholds(self) -> tuple[pd.Series, pd.Series, int]:
        """Per district: 10th and 90th percentile of monthly mean flow over the full years 2000–(last full year)."""
        y1 = await self.last_full_year()
        df = await self.district_monthly_flow()
        ref = df[(df["date"].dt.year >= HISTORY_START.year) & (df["date"].dt.year <= y1)]
        g = ref.groupby("district")["value"]
        return g.quantile(0.1), g.quantile(0.9), y1

    async def status(self) -> tuple[pd.Series, pd.Timestamp]:
        """Flow class per district for the latest complete month (High Flow / Normal / Low Flow)."""
        q10, q90, _ = await self.thresholds()
        last = await self.latest_month(FLOW)
        df = await self.district_monthly_flow()
        cur = df[df["date"] == last].set_index("district")["value"]
        out = {}
        for code, v in cur.items():
            if code in q10.index and pd.notna(v):
                out[code] = "High Flow" if v > q90[code] else "Low Flow" if v < q10[code] else "Normal"
        return pd.Series(out, dtype=object), last

    async def long_term_monthly(self, df: pd.DataFrame) -> pd.DataFrame:
        """district, month (1–12), normal = same-calendar-month mean flow over the full years."""
        y1 = await self.last_full_year()
        ref = df[(df["date"].dt.year >= HISTORY_START.year) & (df["date"].dt.year <= y1)]
        out = ref.assign(month=ref["date"].dt.month).groupby(["district", "month"], as_index=False)["value"].mean()
        return out.rename(columns={"value": "normal"})

    async def district_anomaly(self) -> tuple[pd.Series, pd.Timestamp]:
        df = await self.district_monthly_flow()
        lt = await self.long_term_monthly(df)
        last = await self.latest_month(FLOW)
        cur = df[df["date"] == last].set_index("district")["value"]
        n = lt[lt["month"] == last.month].set_index("district")["normal"]
        return ((cur - n) / n * 100).replace([np.inf, -np.inf], np.nan).dropna(), last

    async def district_ssi(self, scale: int = 3) -> tuple[pd.Series, pd.Timestamp, int]:
        df = await self.district_monthly_flow()
        y1 = await self.last_full_year()
        last = await self.latest_month(FLOW)
        out = {}
        for code, g in df.groupby("district"):
            s = g.set_index("date")["value"].sort_index()
            v = standardized_index(s, scale, (HISTORY_START.year, y1)).get(last)
            if v is not None and pd.notna(v):
                out[str(code)] = float(v)
        return pd.Series(out, dtype=float), last, y1

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        last = await self.latest_month(FLOW)
        q = await self.scope_monthly_flow()
        spark = q.tail(12)
        kpis = [
            self.kpi_unavailable("stations", "gaugingStations", "Active Gauging Stations", "stations", NO_POINTS),
            self.kpi(
                "discharge",
                "discharge",
                "Mean River Discharge",
                q.get(last),
                "m³/s",
                last,
                FLOW,
                decimals=1,
                sublabel=f"{last:%b %Y} · sum over the scope's districts",
                spark=[v for v in (rnd(x, 1) for x in spark) if v is not None],
            ),
        ]
        stage = await self.scope_value(STAGE, await self.latest_month(STAGE))
        kpis.append(
            self.kpi(
                "stage",
                "stage",
                "River Water Level",
                stage,
                "m",
                await self.latest_month(STAGE),
                STAGE,
                decimals=2,
                sublabel="stage height" + ("" if len(self.ctx.weights()) == 1 else " (area-weighted mean)"),
            )
        )
        status, smonth = await self.status()
        mine = status[status.index.isin(self.ctx.filters.scope.district_codes)]
        n = len(mine)
        for kid, metric, label, cls in (
            ("high", "stationsHighFlow", "Districts with High Flow", "High Flow"),
            ("low", "stationsLowFlow", "Districts with Low Flow", "Low Flow"),
        ):
            kpis.append(
                self.kpi(
                    kid,
                    metric,
                    label,
                    float((mine == cls).sum()) if n else None,
                    "districts",
                    smonth,
                    FLOW,
                    decimals=0,
                    sublabel=f"of {n}, {smonth:%b %Y}: {'above the 90th' if cls == 'High Flow' else 'below the 10th'} "
                    "percentile of the district's monthly flows",
                    reason="No district of this scope has a flow record",
                )
            )
        kpis.append(
            self.kpi_unavailable(
                "flood",
                "floodAlerts",
                "Flood Alerts",
                "alerts",
                "No flood alert service or station flood thresholds in the database; peak flows in 9_1 are withheld "
                "(impossible magnitudes)",
            )
        )
        kpis.append(
            self.kpi_unavailable(
                "eflow",
                "eflowCompliance",
                "Ecological Flow Compliance",
                "%",
                "No ecological-flow requirement per river reach in the database (15.1 holds one reserve flow per "
                "district, not per gauged reach)",
            )
        )
        ssc_m = await self.latest_month(SSC)
        kpis.append(
            self.kpi(
                "sediment",
                "sediment",
                "Suspended Sediment",
                await self.scope_value(SSC, ssc_m),
                "mg/L",
                ssc_m,
                SSC,
                decimals=0,
                sublabel=f"{ssc_m:%b %Y}" + ("" if len(self.ctx.weights()) == 1 else " · area-weighted mean"),
            )
        )
        kpis.append(
            self.kpi_unavailable(
                "wqi", "wqi", "River WQ Status", None, "Water quality is on the Water Quality page (district values)"
            )
        )
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        self.use_entity("timeseries", metric)
        handlers = {
            "discharge": self.ts_discharge,
            "fdc": self.ts_fdc,
            "seasonal": self.ts_seasonal,
            "water-level": lambda: self.ts_monthly(STAGE, "water-level", "River water level (stage)", "Water level"),
            "sediment": self.ts_sediment,
            "flow-anomaly": self.ts_anomaly,
        }
        if metric in handlers:
            return await handlers[metric]()
        raise self.no_key(
            "timeseries",
            metric,
            list(handlers),
            {
                "baseflow": "Baseflow separation is not in the database (9_1 has no baseflow; the runoff table's "
                "baseflow component is scaled to a constant model area)"
            },
        )

    async def ts_discharge(self) -> dict[str, Any]:
        start, end = await self.day_window()
        y1 = await self.last_full_year()
        flow = await self.daily_flow(start, end)
        allm = await self.scope_monthly_flow()  # complete months only (monthly_frame caps at the latest complete month)
        # the window's first month is partial (it starts on the day after the newest record a year earlier): its
        # mean over a few days is not a monthly mean (audit step 1), so the step series takes the complete months
        # from the first month of the window to the latest complete month
        monthly = allm[(allm.index >= pd.Timestamp(start.replace(day=1))) & (allm.index <= pd.Timestamp(end))]
        lt = allm[(allm.index.year >= HISTORY_START.year) & (allm.index.year <= y1)]
        lt_m = lt.groupby(lt.index.month).mean()
        down = len(flow) > self.ctx.state.settings.max_series_points
        daily = flow.groupby(flow.index.to_period("M")).mean() if down else flow
        if down:
            daily.index = daily.index.to_timestamp()
        series = [
            self.series("daily", "Daily flow" + (" (monthly means)" if down else ""), daily, nd=2, i=0),
            c.Series(
                key="monthly",
                name="Monthly mean",
                kind="step",
                color=OBSERVED,
                data=[[iso(t.date()), rnd(v, 2)] for t, v in monthly.items()],
            ),
            c.Series(
                key="normal",
                name=f"Long-term mean ({HISTORY_START.year}–{y1})",
                kind="line",
                color=NORMAL,
                dash=True,
                data=[[iso(t.date()), rnd(lt_m.get(t.month), 2)] for t in monthly.index],
            ),
        ]
        if flow.empty:
            for k in ("daily", "monthly", "normal"):
                self.ctx.explain(f"series.{k}", "No day in this period has a flow for every district of the scope")
        return self.series_set(
            "discharge",
            self.entity_title("River discharge"),
            series,
            [c.Axis(unit="m³/s", label="Discharge")],
            [FLOW],
            pd.Timestamp(end),
            note=FLOW_NOTE + " Monthly means: complete months only.",
            baseline=f"{HISTORY_START.year}–{y1} same-month mean",
        )

    async def ts_fdc(self) -> dict[str, Any]:
        newest = await self.newest_day()
        y1 = await self.last_full_year()
        ago = (pd.Timestamp(newest) - pd.DateOffset(years=1)).date()
        curves = [
            ("last", "Last 12 months", ago + dt.timedelta(days=1), newest),
            ("prev", "Previous 12 months", (pd.Timestamp(ago) - pd.DateOffset(years=1)).date(), ago),
            ("lt", f"Long-term ({HISTORY_START.year}–{y1})", HISTORY_START, dt.date(y1, 12, 31)),
        ]
        pcts = np.linspace(0, 100, 101)
        series = []
        for i, (key, name, a, b) in enumerate(curves):
            flow = (await self.daily_flow(a, b)).to_numpy()
            vals: Any = np.percentile(flow, 100 - pcts) if len(flow) else [None] * len(pcts)
            if not len(flow):
                self.ctx.explain(f"series.{key}", f"No day between {a} and {b} has a flow for every district")
            series.append(
                c.Series(
                    key=key,
                    name=name,
                    kind="line",
                    color=[SERIES[0], SERIES[2], NORMAL][i],
                    dash=key == "lt",
                    data=[[rnd(p, 0), rnd(v, 3)] for p, v in zip(pcts, vals, strict=True)],
                )
            )
        return self.series_set(
            "fdc",
            self.entity_title("Flow-duration curves"),
            series,
            [c.Axis(unit="m³/s", label="Discharge", log=True)],
            [FLOW],
            pd.Timestamp(newest),
            x_type="value",
            note=FLOW_NOTE + f" Long-term curve: every day {HISTORY_START.year}–{y1}.",
            x_unit="% of time exceeded",
        )

    async def ts_seasonal(self) -> dict[str, Any]:
        s = await self.scope_monthly_flow()
        y1 = await self.last_full_year()
        last = await self.latest_month(FLOW)
        ref = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)]
        lt = ref.groupby(ref.index.month).mean()
        series = []
        for i, yr in enumerate((last.year, last.year - 1)):
            ys = s[s.index.year == yr]
            data = [[MONTHS[m - 1], rnd(ys.get(pd.Timestamp(yr, m, 1)), 2)] for m in range(1, 13)]
            series.append(c.Series(key=str(yr), name=str(yr), kind="bar", color=YEAR_COLOURS[i], data=data))
        series.append(
            c.Series(
                key="lt",
                name="Long-term average",
                kind="line",
                color=NORMAL,
                dash=True,
                data=[[MONTHS[m - 1], rnd(lt.get(m), 2)] for m in range(1, 13)],
            )
        )
        return self.series_set(
            "seasonal",
            self.entity_title("Average flow by month"),
            series,
            [c.Axis(unit="m³/s", label="Average flow")],
            [FLOW],
            last,
            x_type="category",
            note=FLOW_NOTE + f" Complete months only; long-term = {HISTORY_START.year}–{y1}.",
            baseline=f"{HISTORY_START.year}–{y1}",
        )

    async def ts_monthly(self, var: str, sid: str, title: str, label: str) -> dict[str, Any]:
        start, end = await self.window(var)
        s = await self.scope_series(var, start, end)
        unit = self.unit(var)
        if s.empty:
            self.ctx.explain(f"series.{sid}", f"No {title.lower()} for this scope in the selected period")
        single = len(self.ctx.weights()) == 1
        how = (
            "district value"
            if single
            else "sum over the scope's districts"
            if self.extensive(var)
            else "area-weighted district mean"
        )
        return self.series_set(
            sid,
            self.entity_title(title),
            [self.series(sid, title, s, nd=3, i=0, source=self.ctx.col_source(var).get("source"))],
            [c.Axis(unit=unit, label=label)],
            [var],
            end,
            note=f"Monthly values as stored in 9_1 ({how}).",
        )

    async def ts_sediment(self) -> dict[str, Any]:
        if self.ctx.select(SEDIMENT_KIND) == "conc":
            return await self.ts_monthly(SSC, "sediment", "Suspended sediment concentration", "Concentration")
        return await self.ts_monthly(LOAD, "sediment", "Daily sediment load (monthly mean)", "Load")

    async def ts_anomaly(self) -> dict[str, Any]:
        s = await self.scope_monthly_flow()
        y1 = await self.last_full_year()
        ref = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)]
        normal = ref.groupby(ref.index.month).mean()
        start, end = await self.window(FLOW)
        win = s[(s.index >= start) & (s.index <= end)]
        an = pd.Series({t: (v - normal.get(t.month)) / normal.get(t.month) * 100 for t, v in win.items()})
        pos = an.where(an >= 0)
        neg = an.where(an < 0)

        anomaly_split_reasons(self.ctx, [rnd(v, 1) for v in an.tolist()], "flow anomaly")
        return self.series_set(
            "flow-anomaly",
            self.entity_title("Flow anomaly"),
            [
                self.series("pos", "Above average", pos, nd=1, kind="bar", i=0),
                self.series("neg", "Below average", neg, nd=1, kind="bar", i=3),
            ],
            [c.Axis(unit="%", label="Anomaly")],
            [FLOW],
            end,
            note=FLOW_NOTE + f" Anomaly vs the {HISTORY_START.year}–{y1} same-month mean of the scope's flow.",
            baseline=f"{HISTORY_START.year}–{y1} same-month mean",
        )

    async def forecast(self, metric: str) -> dict[str, Any]:
        """Precomputed statistical forecasts (app.forecasting.pages): river discharge (9_1) and runoff depth."""
        if metric == "flow":
            return fc.series_set(self.ctx, "flow", set_id="flow-forecast", title="River discharge forecast")
        if metric == "runoff":
            return fc.series_set(self.ctx, "runoff", set_id="runoff-forecast", title="Monthly runoff forecast")
        if metric == "flood-probability":  # P(monthly discharge > Q10) from the discharge forecast
            return fc.exceedance_series(
                self.ctx,
                "flow",
                set_id="flood-probability",
                title="Flood probability (river discharge above Q10)",
                name="Flood probability (flow > Q10)",
            )
        raise self.no_key("forecast", metric, ["flow", "runoff", "flood-probability"], {})

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric == "status":
            status, last = await self.status()
            mine = status[status.index.isin(self.ctx.filters.scope.district_codes)]
            counts = [float((mine == s).sum()) for s in STATUS]
            prov = self.ctx.provenance(last.date(), self.src(FLOW), res="monthly")
            prov.pop("asOf")
            prov["aggregation"] = "district"
            return c.CategorySet(
                id="status",
                title=f"Districts by flow status – {last:%b %Y}",
                unit="districts",
                categories=list(STATUS),
                colors=["#2166AC", "#4CAF50", "#F6CF3E"],
                series=[c.CategorySeries(key="districts", name="Districts", values=counts)],
                as_of=iso(last.date()),
                **prov,
            ).dump()
        raise self.no_key(
            "breakdown",
            metric,
            ["status"],
            {
                "eflow": "No ecological-flow requirement per river reach in the database",
                "risk": "No flood / drought risk classification of stations in the database",
                "abstraction": "Licensed abstraction volumes are not in the database",
            },
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        self.use_entity("table", metric)
        if metric == "top-stations":
            return await self.tbl_top(limit)
        if metric == "statistics":
            return await self.tbl_statistics()
        raise self.no_key(
            "table",
            metric,
            ["top-stations", "statistics"],
            {
                "gauge-quality": "Gauge data-quality records are not in the database; water quality is served on the "
                "Water Quality page",
                "alerts": "No surface-water alert service in the database",
            },
        )

    async def tbl_top(self, limit: int | None) -> dict[str, Any]:
        df = await self.district_monthly_flow()
        last = await self.latest_month(FLOW)
        prev = last - pd.DateOffset(years=1)
        cur = df[df["date"] == last].set_index("district")["value"]
        ago = df[df["date"] == prev].set_index("district")["value"]
        sel = self.ctx.filters.scope
        lowest = self.ctx.select(TOP_ORDER) == "lowest"
        rows = []
        for code, v in cur.sort_values(ascending=lowest).items():
            if sel.level != "national" and code not in sel.district_codes:
                continue
            d = districts.by_code(str(code))
            if d is None:
                continue
            p = ago.get(code)
            rows.append(
                {
                    "id": d.code,
                    "station": d.official_name,
                    "code": d.code,
                    "flow": rnd(float(v), 1),
                    "delta": rnd(float(v) - float(p), 1) if p is not None and pd.notna(p) else None,
                }
            )
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        prov = self.ctx.provenance(last.date(), self.src(FLOW), res="monthly")
        prov.pop("asOf")
        prov["aggregation"] = "district"
        return c.TableSet(
            id="top-stations",
            title=f"Districts by mean flow ({'lowest' if lowest else 'highest'}) – {last:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="station", label="District", type="text"),
                c.Column(key="code", label="Code", type="text"),
                c.Column(key="flow", label="Flow", type="number", unit="m³/s", decimals=1, align="right"),
                c.Column(key="delta", label="vs last year", type="delta", unit="m³/s", decimals=1, align="right"),
            ],
            rows=rows[: limit or 10],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(last.date()),
            **prov,
        ).dump()

    async def tbl_statistics(self) -> dict[str, Any]:
        y1 = await self.last_full_year()
        rows = []
        dates = []
        for key, label, var, scale, nd in (
            ("discharge", "River discharge", FLOW, 1 / SECONDS, 1),
            ("stage", "Stage height", STAGE, 1.0, 3),
            ("velocity", "Flow velocity", VELOCITY, 1.0, 3),
            ("ssc", "Suspended sediment concentration", SSC, 1.0, 0),
            ("load", "Daily sediment load", LOAD, 1.0, 0),
        ):
            s = (await self.scope_series(var)) * scale
            last = await self.latest_month(var)
            dates.append(last)
            cur = s[(s.index > last - pd.DateOffset(months=12)) & (s.index <= last)]
            clim = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)]
            annual = clim.groupby(clim.index.year).mean()
            t = sens_slope(annual.tolist())
            cm, lm = (cur.mean() if len(cur) == 12 else None), (clim.mean() if len(clim) else None)
            unit = "m³/s" if var == FLOW else self.unit(var)
            rows.append(
                {
                    "id": key,
                    "variable": label,
                    "current": rnd(cm, nd),
                    "climatology": rnd(lm, nd),
                    "anomaly": rnd((cm - lm) / lm * 100, 1) if cm is not None and lm else None,
                    "trend": rnd(t.slope * 10, nd + 1) if t else None,
                    "unit": unit,
                    "decimals": nd,
                }
            )
        self.ctx.explain("rows.trend", "Too few complete years for a trend (8 needed)")
        last = max(dates)
        return c.TableSet(
            id="statistics",
            title=self.entity_title("Surface-water statistics"),
            columns=[
                c.Column(key="variable", label="Variable", type="text"),
                c.Column(key="current", label="Current (12 m)", type="number", decimals=3, align="right"),
                c.Column(
                    key="climatology", label=f"Mean {HISTORY_START.year}–{y1}", type="number", decimals=3, align="right"
                ),
                c.Column(key="anomaly", label="Anomaly", type="delta", unit="%", decimals=1, align="right"),
                c.Column(
                    key="trend", label="Trend (Sen's slope, per decade)", type="number", decimals=4, align="right"
                ),
                c.Column(key="unit", label="Unit", type="text"),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(last.date()),
            **{
                k: v
                for k, v in self.ctx.provenance(last.date(), self.src(FLOW, STAGE, SSC), res="monthly").items()
                if k != "asOf"
            },
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer in RUNOFF_LAYERS:
            from app.modules.runoff.service import RunoffService

            return await RunoffService(self.ctx).map(layer)  # modelled runoff: the runoff module's layers
        if layer == "discharge":
            return await self.district_map(
                layer, "Mean river discharge", FLOW, "flow", nd=1, transform=lambda v: v / SECONDS, unit="m³/s"
            )
        if layer == "flow-status":
            status, last = await self.status()
            return self._class_map(layer, f"Flow status – {last:%b %Y}", status, last, "flowStatus")
        if layer == "flow-anomaly":
            an, last = await self.district_anomaly()
            return self._value_map(
                layer, f"Flow anomaly vs the same-month mean – {last:%b %Y}", an, last, "anomalyPct", "%", 0
            )
        if layer == "ssi":
            ssi, last, y1 = await self.district_ssi(3)
            body = self._value_map(layer, f"SSI-3 (from the stored discharge) – {last:%b %Y}", ssi, last, "spi", "—", 2)
            body["baseline"] = f"{HISTORY_START.year}–{y1} calibration (gamma per calendar month)"
            return body
        raise self.no_key(
            "map",
            layer,
            [*MAP_LAYERS, *RUNOFF_LAYERS],
            {
                "stations": NO_POINTS + " (district flows: the `discharge` layer)",
                "catchment-runoff": "No catchment boundaries or catchment codes in the database (districts only)",
            },
        )

    def _value_map(
        self, layer: str, title: str, vals: pd.Series, last: pd.Timestamp, scale: str, unit: str, nd: int
    ) -> dict[str, Any]:
        sel = self.ctx.filters.scope
        out = {
            str(k): rnd(float(v), nd)
            for k, v in vals.items()
            if pd.notna(v) and (sel.level == "national" or k in sel.district_codes)
        }
        if not out:
            self.ctx.explain("values", "No district of this scope has a flow record for this month")
        prov = self.ctx.provenance(last.date(), self.src(FLOW), res="monthly")
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

    def _class_map(self, layer: str, title: str, vals: pd.Series, last: pd.Timestamp, scale: str) -> dict[str, Any]:
        sel = self.ctx.filters.scope
        out = {str(k): v for k, v in vals.items() if sel.level == "national" or k in sel.district_codes}
        prov = self.ctx.provenance(last.date(), self.src(FLOW), res="monthly")
        prov["aggregation"] = "district"
        return c.MapLayer(
            id=layer,
            title=title,
            kind="choropleth",
            level="municipality",
            scale=scale,
            unit="—",
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
        df = await self.district_monthly_flow()
        last = await self.latest_month(FLOW)
        cur = df[(df["district"] == d.code) & (df["date"] == last)]["value"]
        status, _ = await self.status()
        ssi, _, _ = await self.district_ssi(3)
        sv = ssi.get(d.code)
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            c.EntityField(
                label=f"Mean flow ({last:%b %Y})",
                value=rnd(float(cur.iloc[0]), 1) if len(cur) else None,
                unit="m³/s",
                as_of=iso(last.date()),
            ),
            c.EntityField(label=f"Flow status ({last:%b %Y})", value=status.get(d.code), scale="flowStatus"),
            c.EntityField(label=f"SSI-3 ({last:%b %Y})", value=rnd(sv, 2)),
            await self.field("Stage height", STAGE, d.code, nd=2),
            await self.field("Flow velocity", VELOCITY, d.code, nd=3),
            await self.field("Suspended sediment", SSC, d.code, nd=0),
            await self.field("Catchment area", "cc_catchment_area_km2", d.code, nd=0),
            await self.field("Tree cover", "cc_tree_cover_pct", d.code, nd=1),
            await self.field("Built-up / urban", "cc_builtup_urban_pct", d.code, nd=1),
        ]
        cls = drought_class_or_none(sv)
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type="district",
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code} · district flow record (9_1)",
            title=d.official_name,
            fields=fields,
            image="river",
            **({"status": drought_status(sv)["status"]} if cls is not None else {}),
            as_of=iso(last.date()),
            source=self.src(FLOW, STAGE, SSC, "cc_catchment_area_km2"),
        ).dump()


async def prewarm(data: Any) -> None:
    await data.daily_monthly_stats(FLOW, ())
    await data.daily_monthly_stats(LOAD, ())
    for var in (STAGE, VELOCITY, SSC):
        await data.monthly(var)
