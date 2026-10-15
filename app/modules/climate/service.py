"""Climate Change Indicators (contract module `climate`, spec §4; DB Module 2).

Sources (canonical, app/registry/datasets.yaml): temperatures, anomaly and hot days from `4_1`; ETCCDI rainfall
indices from `4_2`; annual and seasonal rainfall from `4_3`; ET₀ / PET from `meteorological_drought_master`; runoff
depth and AET from `runoff_variables_master`; SPI-3 from `6.2`. Withheld (D5): all of `4_4`, `4_5`, `4_6`
(evaporation magnitudes, irrigation, annual streamflow) — so pan evaporation, VPD and irrigation are unavailable and
runoff indicators come from `runoff_variables_master` instead. No projections or vulnerability data exist.

Methods (docs/DATA_MAPPING.md §3): annual values use full years only (2000 – last full year); trends are Sen's slope
with Mann-Kendall significance (`pymannkendall`); temperature anomaly = mean of the DB's monthly anomaly (2000–2026
baseline); other anomalies vs the 2000–(last full year) mean; area-weighted district means (≥ 95 % coverage).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from app.core import districts
from app.core.aggregate import MIN_COVERAGE, coverage_reason, period_gap, short_of_coverage, weighted, weighted_scalar
from app.core.data import DataAccess
from app.core.errors import DataUnavailable
from app.core.palettes import DROUGHT, NEGATIVE, OBSERVED, POSITIVE, SERIES
from app.core.provenance import Resolution, iso
from app.core.stats import rnd, sens_slope, trend_gap, trend_line
from app.core.thresholds import drought_class, drought_label
from app.modules.common import Ctx, Selector, unknown
from app.modules.rainfall.service import HISTORY_START, RainfallService, _months
from app.schemas import contract as c

MODULE = "climate"
SEASONS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
SEVERE = ("D2", "D3", "D4")  # spec §3.4: severe drought or worse = SPI ≤ −1.3 (thresholds.json)
SEASONAL_VAR = Selector("var", ("temperature", "rainfall"), "temperature")
TREND_VAR = Selector("var", ("temperature", "rainfall", "et", "streamflow"), "temperature")
HEAT_KIND = Selector("kind", ("hot", "cold"), "hot")
RAIN_KIND = Selector("kind", ("r20", "rx1"), "r20")
# indicator-trends is observed; a scenario asks for projections, which the database does not have (→ 501)
SCENARIO = Selector("scenario", ("observed", "rcp45", "rcp85"), "observed")
SELECTORS: dict[tuple[str, str], tuple[Selector, ...]] = {
    ("breakdown", "seasonal-change"): (SEASONAL_VAR,),
    ("timeseries", "national-trend"): (TREND_VAR,),
    ("timeseries", "heat-days"): (HEAT_KIND,),
    ("timeseries", "extreme-rain"): (RAIN_KIND,),
    ("timeseries", "indicator-trends"): (SCENARIO,),
}
# national-trend `var` → (title, axis label, unit, decimals, source variable, note on the source)
TREND_VARS: dict[str, tuple[str, str, str, int, str, str]] = {
    "temperature": ("Mean annual temperature", "Mean temperature", "°C", 2, "tavg", "Daily Tavg (4_1)"),
    "rainfall": ("Annual rainfall", "Annual total", "mm", 1, "rain_annual", "Mean annual precipitation (4_3)"),
    "et": (
        "Annual reference evapotranspiration (ET₀)",
        "Annual total",
        "mm",
        1,
        "et0_daily",
        "Sum of daily ET₀ (meteorological_drought_master; the table lacks 31 December)",
    ),
    "streamflow": (
        "Annual runoff",
        "Annual total",
        "mm",
        1,
        "runoff_depth_daily",
        "Modelled runoff depth (runoff_variables_master), not gauged streamflow",
    ),
}


def unavailable(what: str, missing: str) -> DataUnavailable:
    return DataUnavailable(what, missing=[missing])


def _d(ts: Any) -> dt.date:
    d: dt.date = pd.Timestamp(ts).date()
    return d


class ClimateService:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.data: DataAccess = ctx.data
        self.rain = RainfallService(ctx)

    # ---------------------------------------------------------------- helpers

    def src(self, *variables: str) -> list[str]:
        return self.rain.src(*variables)

    def _prov(
        self, as_of: dt.date | None, *variables: str, baseline: bool = False, res: Resolution = "daily"
    ) -> dict[str, Any]:
        p = self.ctx.provenance(as_of, self.src(*variables), baseline=baseline, res=res)
        p.pop("asOf")
        return p

    def kpi_extra(self, as_of: dt.date | None, *variables: str, res: Resolution = "daily") -> dict[str, Any]:
        return {"as_of": iso(as_of), "source": self.src(*variables), **self.ctx.stamp(as_of, res)}

    async def last_full_year(self) -> int:
        newest = await self.rain.newest_daily()
        return newest.year if newest >= dt.date(newest.year, 12, 30) else newest.year - 1

    async def years(self) -> tuple[int, int]:
        """Full years for annual series: whole record unless `from`/`to` narrow it."""
        y1 = await self.last_full_year()
        win = self.ctx.filters.window
        y0 = max(HISTORY_START.year, win.date_from.year) if win.date_from else HISTORY_START.year
        if win.date_to:
            y1 = min(y1, win.date_to.year)
        return y0, y1

    async def annual_daily(self, var: str, how: str) -> pd.DataFrame:
        """District-year values from a daily variable: `mean` (Σ sum / Σ days) or `sum`.

        Columns: district, date, value.
        """
        dm = await self.data.daily_monthly_stats(var, ())
        g = dm.assign(year=dm["date"].dt.year).groupby(["district", "year"], as_index=False)[["sum", "days"]].sum()
        g["value"] = g["sum"] / g["days"] if how == "mean" else g["sum"]
        g["date"] = pd.to_datetime(g["year"].astype(str) + "-01-01")
        return g[["district", "date", "value"]]

    async def annual_agg(self, df: pd.DataFrame, y0: int, y1: int) -> pd.Series:
        """Area-weighted scope series indexed by year."""
        df = df[(df["date"].dt.year >= y0) & (df["date"].dt.year <= y1)]
        agg = weighted(df, self.ctx.weights())
        return agg.set_index(agg["date"].dt.year)["value"]

    async def annual_var(self, var: str, y0: int, y1: int) -> pd.Series:
        return await self.annual_agg(await self.data.annual(var), y0, y1)

    async def monthly_mean(self, var: str) -> pd.DataFrame:
        """District-month mean of a daily variable."""
        dm = await self.data.daily_monthly_stats(var, ())
        return dm.assign(value=dm["sum"] / dm["days"])[["district", "date", "value"]]

    async def temp_anomaly_12m(self) -> tuple[float | None, pd.Series, dt.date]:
        """Mean monthly temperature anomaly over the last 12 complete months: scope value, per district, last month."""
        last = await self.rain.latest_complete_month()
        df = await self.data.monthly("temp_anomaly")
        lo = pd.Timestamp(last) - pd.DateOffset(months=11)
        win = df[(df["date"] >= lo) & (df["date"] <= pd.Timestamp(last))]
        per = win.groupby("district")["value"].mean()
        return weighted_scalar(per, self.ctx.weights()), per, last

    @staticmethod
    def series(years: range, s: pd.Series, nd: int = 2) -> list[list[Any]]:
        return [[f"{y}-01-01", rnd(s.get(y), nd)] for y in years]

    @staticmethod
    def trend_series(years: range, s: pd.Series, nd: int = 2) -> list[list[Any]]:
        line = trend_line([s.get(y) for y in years])
        return [[f"{y}-01-01", rnd(v, nd)] for y, v in zip(years, line, strict=True)]

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        y0, y1 = HISTORY_START.year, await self.last_full_year()
        yr_end = dt.date(y1, 12, 31)
        kpis: list[c.Kpi] = []

        ta, _, last = await self.temp_anomaly_12m()
        kpis.append(
            c.Kpi(
                id="tempAnomaly",
                metric="tempAnomaly",
                label="Temperature Anomaly",
                sublabel=f"12 months to {last:%b %Y}, vs 2000–2026",
                value=rnd(ta, 2),
                unit="°C",
                decimals=1,
                **self.kpi_extra(last, "temp_anomaly", res="monthly"),
            )
        )
        tavg = await self.annual_agg(await self.annual_daily("tavg", "mean"), y0, y1)
        tr = sens_slope([tavg.get(y) for y in range(y0, y1 + 1)])
        if gap := trend_gap([tavg.get(y) for y in range(y0, y1 + 1)], "annual mean temperature"):
            self.ctx.explain("kpis.tempTrend", gap)
        kpis.append(
            c.Kpi(
                id="tempTrend",
                metric="tempTrend",
                label="Warming Trend",
                sublabel=f"Sen's slope {y0}–{y1}" + (f", p = {tr.p:.2f}" if tr else ""),
                value=rnd(tr.slope * 10, 2) if tr else None,
                unit="°C/decade",
                decimals=2,
                **self.kpi_extra(yr_end, "tavg", res="annual"),
            )
        )
        rain = await self.annual_var("rain_annual", y0, y1)
        r_mean = rain.mean()
        kpis.append(
            c.Kpi(
                id="rainChange",
                metric="rainfallAnomaly",
                label="Rainfall Change",
                sublabel=f"{y1} vs {y0}–{y1} mean ({r_mean:.0f} mm)" if not np.isnan(r_mean) else None,
                value=rnd((rain.get(y1) - r_mean) / r_mean * 100, 1) if y1 in rain.index and r_mean else None,
                unit="%",
                decimals=0,
                **self.kpi_extra(yr_end, "rain_annual", res="annual"),
            )
        )
        et0 = await self.annual_agg(await self.annual_daily("et0_daily", "sum"), y0, y1)
        kpis.append(
            c.Kpi(
                id="et0",
                metric="et0",
                label="Reference ET₀ (annual)",
                sublabel=f"{y1}",
                value=rnd(et0.get(y1), 0),
                unit="mm",
                decimals=0,
                delta=rnd(et0.get(y1) - et0.mean(), 0) if y1 in et0.index else None,
                delta_unit=" mm",
                delta_label=f"vs {y0}–{y1} mean",
                **self.kpi_extra(yr_end, "et0_daily", res="annual"),
            )
        )
        q = await self.annual_agg(await self.annual_daily("runoff_depth_daily", "sum"), y0, y1)
        q_mean = q.mean()
        kpis.append(
            c.Kpi(
                id="runoff",
                metric="runoffAnomaly",
                label="Mean Annual Runoff",
                sublabel=f"{y1} vs {y0}–{y1} mean ({q_mean:.0f} mm)" if not np.isnan(q_mean) else None,
                value=rnd((q.get(y1) - q_mean) / q_mean * 100, 1) if y1 in q.index and q_mean else None,
                unit="%",
                decimals=0,
                **self.kpi_extra(yr_end, "runoff_depth_daily", res="annual"),
            )
        )
        for kid, var, metric, label, unit in (
            ("hotDays", "hot_days", "hotDays", "Hot Days (> 35 °C)", "days"),
            ("r20", "r20mm", "heavyRainDays", "Heavy Rain Days (R20mm)", "days"),
            ("cdd", "cdd", "cdd", "Consecutive Dry Days", "days"),
        ):
            s = await self.annual_var(var, y0, y1)
            kpis.append(
                c.Kpi(
                    id=kid,
                    metric=metric,
                    label=label,
                    sublabel=f"{y1}, district mean",
                    value=rnd(s.get(y1), 1),
                    unit=unit,
                    decimals=0,
                    delta=rnd(s.get(y1) - s.mean(), 1) if y1 in s.index else None,
                    delta_unit=" days",
                    delta_label=f"vs {y0}–{y1} mean",
                    **self.kpi_extra(yr_end, var, res="annual"),
                )
            )
        as_of = max(dt.date.fromisoformat(k.as_of) for k in kpis if k.as_of)
        body = c.KpiSet(
            module=MODULE,
            as_of=iso(as_of) or "",
            scope=self.ctx.scope,
            kpis=kpis,
            components=[
                c.Component(
                    key=k.id,
                    as_of=k.as_of,
                    source=k.source or [],
                    is_stale=bool(k.is_stale),
                    **({"period_end": k.period_end} if k.period_end else {}),
                )
                for k in kpis
                if k.as_of
            ],
            **self._prov(
                as_of,
                "temp_anomaly",
                "tavg",
                "rain_annual",
                "et0_daily",
                "runoff_depth_daily",
                "hot_days",
                "r20mm",
                "cdd",
                baseline=True,
                res="monthly",
            ),
        )
        return body.dump()

    # ---------------------------------------------------------------- time series

    async def timeseries(self, metric: str) -> dict[str, Any]:
        handlers = {
            "temperature-long": self.ts_temperature_long,
            "national-trend": self.ts_national_trend,
            "extremes": self.ts_extremes,
            "heat-days": self.ts_heat_days,
            "extreme-rain": self.ts_extreme_rain,
            "evaporation": self.ts_evaporation,
            "streamflow-linkage": self.ts_streamflow_linkage,
            "drought-frequency": self.ts_drought_frequency,
            "indicator-trends": self.ts_indicator_trends,
        }
        if metric == "projections":
            raise unavailable("No climate projections (RCP scenarios) in the database", "projections")
        if metric == "irrigation":
            raise unavailable(
                "Irrigation demand is withheld: table 4_5 is built on implausible ET₀ values (4_4)", "irrigation_demand"
            )
        if metric not in handlers:
            raise unknown("metric", metric, [*handlers, "projections", "irrigation"])
        return (await handlers[metric]()).dump()

    async def ts_temperature_long(self) -> c.SeriesSet:
        w = self.ctx.weights()
        last = await self.rain.latest_complete_month()
        win = self.ctx.filters.window
        first = pd.Timestamp((win.date_from or HISTORY_START).replace(day=1))
        lt = min(pd.Timestamp(last), pd.Timestamp(win.date_to.replace(day=1))) if win.date_to else pd.Timestamp(last)
        ms = _months(first.date(), lt.date())
        out = {}
        for var in ("tmax", "tavg", "tmin"):
            out[var] = weighted(await self.monthly_mean(var), w).set_index("date")["value"]
        anom = weighted(await self.data.monthly("temp_anomaly"), w).set_index("date")["value"]
        return c.SeriesSet(
            id="temperature-long",
            title="Temperature",
            x_type="time",
            axes=[c.Axis(unit="°C", label="Temperature"), c.Axis(unit="°C", label="Anomaly")],
            series=[
                c.Series(
                    key="anomaly",
                    name="Tavg anomaly (vs 2000–2026)",
                    kind="bar",
                    axis=1,
                    color=NEGATIVE,
                    data=[[iso(m.date()), rnd(anom.get(m), 2)] for m in ms],
                ),
                c.Series(
                    key="tmax",
                    name="Tmax",
                    kind="line",
                    axis=0,
                    color=SERIES[3],
                    data=[[iso(m.date()), rnd(out["tmax"].get(m), 1)] for m in ms],
                ),
                c.Series(
                    key="tavg",
                    name="Tavg",
                    kind="line",
                    axis=0,
                    color=OBSERVED,
                    data=[[iso(m.date()), rnd(out["tavg"].get(m), 1)] for m in ms],
                ),
                c.Series(
                    key="tmin",
                    name="Tmin",
                    kind="line",
                    axis=0,
                    color=SERIES[0],
                    data=[[iso(m.date()), rnd(out["tmin"].get(m), 1)] for m in ms],
                ),
            ],
            as_of=iso(lt.date()),
            note="Monthly means of daily values; anomaly vs the 2000–2026 mean (data start in 2000). Complete months "
            "only.",
            **self._prov(lt.date(), "tmax", "temp_anomaly", baseline=True, res="monthly"),
        )

    async def trend_var_series(self, var: str, y0: int, y1: int) -> pd.Series:
        if var == "temperature":
            return await self.annual_agg(await self.annual_daily("tavg", "mean"), y0, y1)
        if var == "rainfall":
            return await self.annual_var("rain_annual", y0, y1)
        daily = "et0_daily" if var == "et" else "runoff_depth_daily"
        return await self.annual_agg(await self.annual_daily(daily, "sum"), y0, y1)

    async def ts_national_trend(self) -> c.SeriesSet:
        var = self.ctx.select(TREND_VAR)
        title, axis_label, unit, nd, source_var, source_note = TREND_VARS[var]
        y0, y1 = await self.years()
        yrs = range(y0, y1 + 1)
        t = await self.trend_var_series(var, y0, y1)
        vals = [t.get(y) for y in yrs]
        line = trend_line(vals)
        resid = [v - f for v, f in zip(vals, line, strict=True) if v is not None and f is not None]
        sd = float(np.std(resid, ddof=1)) if len(resid) > 2 else None
        ma = pd.Series(vals, index=list(yrs), dtype=float).rolling(5, min_periods=5).mean()
        tr = sens_slope(vals)
        if gap := trend_gap(vals, axis_label.lower()):
            self.ctx.explain("series.band", gap)
        n_years = sum(1 for v in vals if v is not None and not pd.isna(v))
        self.ctx.explain(
            "series.ma5", f"A 5-year moving average needs 5 consecutive years; this scope and period have {n_years}"
        )
        per = f"{unit}/decade"
        return c.SeriesSet(
            id="national-trend",
            title=title,
            x_type="time",
            axes=[c.Axis(unit=unit, label=axis_label)],
            series=[
                c.Series(
                    key="band",
                    name="Trend ± 1 s.d.",
                    kind="band",
                    color="#FDDBC7",
                    data=[
                        [
                            f"{y}-01-01",
                            rnd(f - sd, nd) if f is not None and sd else None,
                            rnd(f + sd, nd) if f is not None and sd else None,
                        ]
                        for y, f in zip(yrs, line, strict=True)
                    ],
                ),
                c.Series(
                    key="annual",
                    name="Annual",
                    kind="line",
                    color=OBSERVED,
                    symbol=True,
                    data=self.series(yrs, t, nd),
                ),
                c.Series(
                    key="ma5",
                    name="5-year moving average",
                    kind="line",
                    color=SERIES[3],
                    smooth=True,
                    data=[[f"{y}-01-01", rnd(ma.get(y), nd)] for y in yrs],
                ),
            ],
            as_of=f"{y1}-01-01",
            note=(
                f"Sen's slope {tr.slope * 10:+.{nd}f} {per} (Mann-Kendall p = {tr.p:.3f}, {tr.trend}); "
                f"full years {y0}–{y1}. {source_note}."
            )
            if tr
            else f"Full years {y0}–{y1}. {source_note}.",
            **self._prov(dt.date(y1, 12, 31), source_var, res="annual"),
        )

    async def ts_extremes(self) -> c.SeriesSet:
        y0, y1 = await self.years()
        yrs = range(y0, y1 + 1)
        s = {v: await self.annual_var(v, y0, y1) for v in ("cdd", "cwd", "rx1day", "rx5day")}
        return c.SeriesSet(
            id="extremes",
            title="Rainfall extremes (ETCCDI)",
            x_type="time",
            axes=[c.Axis(unit="days", label="Spell length"), c.Axis(unit="mm", label="Max rainfall")],
            series=[
                c.Series(key="cdd", name="CDD", kind="bar", axis=0, color=NEGATIVE, data=self.series(yrs, s["cdd"], 1)),
                c.Series(key="cwd", name="CWD", kind="bar", axis=0, color=POSITIVE, data=self.series(yrs, s["cwd"], 1)),
                c.Series(
                    key="rx1day",
                    name="Rx1day",
                    kind="line",
                    axis=1,
                    color=SERIES[3],
                    data=self.series(yrs, s["rx1day"], 1),
                ),
                c.Series(
                    key="rx5day",
                    name="Rx5day",
                    kind="line",
                    axis=1,
                    color=SERIES[4],
                    data=self.series(yrs, s["rx5day"], 1),
                ),
            ],
            as_of=f"{y1}-01-01",
            note="Area-weighted district means of annual indices; full years only.",
            **self._prov(dt.date(y1, 12, 31), "cdd", "rx1day", res="annual"),
        )

    async def _count_with_trend(
        self,
        var: str,
        sid: str,
        title: str,
        label: str,
        axis_label: str,
        unit: str = "days",
        from_daily: bool = False,
    ) -> c.SeriesSet:
        """Annual values of `var` (an annual variable, or the yearly sum of a daily one) with their Sen's-slope line."""
        y0, y1 = await self.years()
        yrs = range(y0, y1 + 1)
        if from_daily:
            s = await self.annual_agg(await self.annual_daily(var, "sum"), y0, y1)
        else:
            s = await self.annual_var(var, y0, y1)
        tr = sens_slope([s.get(y) for y in yrs])
        if gap := trend_gap([s.get(y) for y in yrs], label.lower()):
            self.ctx.explain("series.trend", gap)
        return c.SeriesSet(
            id=sid,
            title=title,
            x_type="time",
            axes=[c.Axis(unit=unit, label=axis_label)],
            series=[
                c.Series(key="annual", name=label, kind="bar", color=SERIES[0], data=self.series(yrs, s, 1)),
                c.Series(
                    key="trend",
                    name="Trend",
                    kind="line",
                    color=SERIES[3],
                    dash=True,
                    data=self.trend_series(yrs, s, 2),
                ),
            ],
            as_of=f"{y1}-01-01",
            note=(f"Sen's slope {tr.slope * 10:+.1f} {unit}/decade (p = {tr.p:.3f}). " if tr else "")
            + "Area-weighted district mean; full years only.",
            **self._prov(dt.date(y1, 12, 31), var, res="annual"),
        )

    async def ts_heat_days(self) -> c.SeriesSet:
        if self.ctx.select(HEAT_KIND) == "cold":
            return await self._count_with_trend(
                "frost_day",
                "heat-days",
                "Frost nights (Tmin < 0 °C)",
                "Annual count",
                "Nights with Tmin < 0 °C",
                from_daily=True,
            )
        return await self._count_with_trend(
            "hot_days", "heat-days", "Hot days (Tmax > 35 °C)", "Annual count", "Days with Tmax > 35 °C"
        )

    async def ts_extreme_rain(self) -> c.SeriesSet:
        if self.ctx.select(RAIN_KIND) == "rx1":
            return await self._count_with_trend(
                "rx1day", "extreme-rain", "Maximum 1-day rainfall (Rx1day)", "Rx1day", "Max 1-day rainfall", unit="mm"
            )
        return await self._count_with_trend(
            "r20mm", "extreme-rain", "Heavy rain days (R20mm)", "Heavy rain days (R20mm)", "Days ≥ 20 mm"
        )

    async def ts_evaporation(self) -> c.SeriesSet:
        y0, y1 = await self.years()
        yrs = range(y0, y1 + 1)
        et0 = await self.annual_agg(await self.annual_daily("et0_daily", "sum"), y0, y1)
        pet = await self.annual_agg(await self.annual_daily("pet_daily", "sum"), y0, y1)
        aet = await self.annual_agg(await self.annual_daily("aet_daily", "sum"), y0, y1)
        reason = "Pan evaporation and VPD are withheld: table 4_4 values are implausible (ET₀ 23–97× too small)"
        return c.SeriesSet(
            id="evaporation",
            title="Evaporation",
            x_type="time",
            axes=[c.Axis(unit="mm/yr", label="Evaporation"), c.Axis(unit="kPa", label="VPD")],
            series=[
                c.Series(key="et0", name="ET₀", kind="line", axis=0, color=SERIES[3], data=self.series(yrs, et0, 0)),
                c.Series(key="pet", name="PET", kind="line", axis=0, color=SERIES[2], data=self.series(yrs, pet, 0)),
                c.Series(
                    key="aet",
                    name="AET (water balance)",
                    kind="line",
                    axis=0,
                    color=SERIES[1],
                    data=self.series(yrs, aet, 0),
                ),
                c.Series(
                    key="pan",
                    name="Pan evaporation (Class A)",
                    kind="line",
                    axis=0,
                    color=SERIES[4],
                    data=[[f"{y}-01-01", None] for y in yrs],
                ),
                c.Series(
                    key="vpd",
                    name="Vapour pressure deficit",
                    kind="line",
                    axis=1,
                    color=SERIES[5],
                    data=[[f"{y}-01-01", None] for y in yrs],
                ),
            ],
            as_of=f"{y1}-01-01",
            note="ET₀ and PET: annual sums of the daily values (31 December is missing in the source table); AET: "
            "modelled catchment evapotranspiration. The calculation methods are not documented in the database.",
            unavailable=[
                c.UnavailableField(field="series.pan", reason=reason),
                c.UnavailableField(field="series.vpd", reason=reason),
            ],
            **self._prov(dt.date(y1, 12, 31), "et0_daily", "pet_daily", "aet_daily", res="annual"),
        )

    async def ts_streamflow_linkage(self) -> c.SeriesSet:
        y0, y1 = await self.years()
        yrs = range(y0, y1 + 1)
        q = await self.annual_agg(await self.annual_daily("runoff_depth_daily", "sum"), y0, y1)
        mean = q.mean()
        return c.SeriesSet(
            id="streamflow-linkage",
            title="Runoff and climate",
            x_type="time",
            axes=[c.Axis(unit="mm/yr", label="Runoff"), c.Axis(unit="%", label="Anomaly")],
            series=[
                c.Series(
                    key="runoff",
                    name="Mean annual runoff",
                    kind="bar",
                    axis=0,
                    color=SERIES[0],
                    data=self.series(yrs, q, 1),
                ),
                c.Series(
                    key="anomaly",
                    name="Runoff anomaly",
                    kind="line",
                    axis=1,
                    color=SERIES[3],
                    data=[
                        [f"{y}-01-01", rnd((q.get(y) - mean) / mean * 100, 1) if y in q.index else None] for y in yrs
                    ],
                ),
            ],
            as_of=f"{y1}-01-01",
            note=f"Modelled runoff depth (runoff_variables_master); anomaly vs {y0}–{y1} mean. Table 4_6 withheld.",
            **{**self._prov(dt.date(y1, 12, 31), "runoff_depth_daily", res="annual"), "baseline": f"{y0}–{y1}"},
        )

    async def severe_drought_by_year(self, y0: int, y1: int) -> tuple[pd.DataFrame, pd.Series]:
        """Per full year: % of the scope's area in D2, D3 and D4 (mean of the monthly area shares; district SPI-3
        classified with the spec §3.4 thresholds), and the number of district-months in D2 or worse.

        Districts are classified one by one, then their areas added up: averaging SPI first cancels out regional
        drought (KZN in D1–D2 while the national mean SPI-3 stays above −1). A month counts when districts with data
        cover ≥ 95 % of the area (shares are of that area); a year needs all 12 months.
        """
        w = self.ctx.weights()
        total = sum(w.values())
        df = await self.data.monthly("spi_3")
        df = df[df["district"].isin(w.keys()) & df["value"].notna() & df["date"].dt.year.between(y0, y1)].copy()
        df["cls"] = df["value"].map(drought_class)
        df["w"] = df["district"].map(w)
        covered = df.groupby("date")["w"].sum()
        good = covered[covered >= MIN_COVERAGE * total]
        df = df[df["date"].isin(good.index)]
        if df.empty:
            return pd.DataFrame(columns=list(SEVERE), dtype=float), pd.Series(dtype=float)
        area = df.pivot_table(index="date", columns="cls", values="w", aggfunc="sum").reindex(columns=list(SEVERE))
        share = area.fillna(0.0).div(good.reindex(area.index), axis=0) * 100
        years = share.index.year
        full = share.groupby(years).size() == 12
        yearly = share.groupby(years).mean()[full]
        n = df[df["cls"].isin(SEVERE)].groupby(df["date"].dt.year).size()
        months = pd.Series({y: float(n.get(y, 0)) for y in yearly.index}, dtype=float)
        return yearly, months

    async def ts_drought_frequency(self) -> c.SeriesSet:
        y0, y1 = await self.years()
        yrs = range(y0, y1 + 1)
        yearly, months = await self.severe_drought_by_year(y0, y1)
        severe = yearly.sum(axis=1) if not yearly.empty else pd.Series(dtype=float)
        why = f"No SPI-3 for districts covering ≥ {MIN_COVERAGE:.0%} of this scope's area in every month of a year"
        for key in (*(k.lower() for k in SEVERE), "months"):
            self.ctx.explain(f"series.{key}", f"{why} ({y0}–{y1})")
        if gap := trend_gap([severe.get(y) for y in yrs], "area-in-drought shares"):
            self.ctx.explain("series.trend", gap)
        empty = pd.Series(dtype=float)
        return c.SeriesSet(
            id="drought-frequency",
            title="Drought frequency: area in severe drought or worse (SPI-3 ≤ −1.3)",
            x_type="time",
            axes=[
                c.Axis(unit="%", label="Area in class (% of area, mean of months)", min=0),
                c.Axis(unit="district-months", label="District-months in D2 or worse", min=0),
            ],
            series=[
                *(
                    c.Series(
                        key=k.lower(),
                        name=drought_label(k),
                        kind="bar",
                        stack="area",
                        axis=0,
                        color=DROUGHT[k],
                        data=self.series(yrs, yearly.get(k, empty), 1),
                    )
                    for k in SEVERE
                ),
                c.Series(
                    key="trend",
                    name="Trend (area in D2–D4)",
                    kind="line",
                    axis=0,
                    color=SERIES[3],
                    dash=True,
                    data=self.trend_series(yrs, severe, 2),
                ),
                c.Series(
                    key="months",
                    name="District-months in D2 or worse",
                    kind="line",
                    axis=1,
                    color=NEGATIVE,
                    data=self.series(yrs, months, 0),
                ),
            ],
            as_of=f"{y1}-01-01",
            note="Each district's SPI-3 is classified (spec §3.4: D2 ≤ −1.3, D3 ≤ −1.6, D4 ≤ −2.0) and the areas in "
            "each class are added up per month, then averaged over the year (% of area-months). Bars: share of the "
            "scope's area per class; line: number of district-months in D2 or worse. Full years only.",
            **self._prov(dt.date(y1, 12, 31), "spi_3", res="annual"),
        )

    async def ts_indicator_trends(self) -> c.SeriesSet:
        scenario = self.ctx.select(SCENARIO)
        if scenario != "observed":
            raise unavailable(
                f"No climate projections ({scenario.upper()}) in the database: indicator trends are observed only "
                "(request them without a scenario)",
                f"projections:{scenario}",
            )
        _, y1 = await self.years()
        base = 2020
        if y1 <= base:
            raise unavailable("Indicator trends need full years after 2020", "years after 2020")
        yrs = range(base, y1 + 1)
        t = await self.annual_agg(await self.annual_daily("tavg", "mean"), base, y1)
        rain = await self.annual_var("rain_annual", base, y1)
        hot = await self.annual_var("hot_days", base, y1)
        r20 = await self.annual_var("r20mm", base, y1)
        yearly, months = await self.severe_drought_by_year(base, y1)
        dm = yearly.sum(axis=1) if not yearly.empty else pd.Series(dtype=float)  # % of area in D2–D4

        def pct(s: pd.Series, key: str, what: str, zero_detail: str = "") -> list[list[Any]]:
            b = s.get(base)
            if b is None or pd.isna(b):
                self.ctx.explain(f"series.{key}", f"No {what} for {base} (the base year) in this scope")
            elif b == 0:
                self.ctx.explain(
                    f"series.{key}",
                    f"{what[0].upper()}{what[1:]} was 0 in {base} (the base year) in this scope{zero_detail}, so a "
                    f"% change from {base} is undefined",
                )
            return [[f"{y}-01-01", rnd((s.get(y) - b) / b * 100, 1) if b and y in s.index else None] for y in yrs]

        if base not in t.index or pd.isna(t.get(base)):
            self.ctx.explain("series.temp", f"No annual mean temperature for {base} (the base year) in this scope")
        none_base = " (no district-month reached D2, SPI-3 ≤ −1.3)" if months.get(base) == 0 else ""

        return c.SeriesSet(
            id="indicator-trends",
            title=f"Change vs {base}",
            x_type="time",
            axes=[
                c.Axis(unit="%", label=f"Change vs {base}"),
                c.Axis(unit="°C", label=f"Temperature change vs {base}"),
            ],
            series=[
                c.Series(
                    key="temp",
                    name="Mean temperature (°C change)",
                    kind="line",
                    axis=1,
                    color=SERIES[3],
                    data=[[f"{y}-01-01", rnd(t.get(y) - t.get(base), 2) if base in t.index else None] for y in yrs],
                ),
                c.Series(
                    key="rain", name="Annual rainfall", kind="line", color=SERIES[0], data=pct(rain, "rain", "rainfall")
                ),
                c.Series(
                    key="hot",
                    name="Extreme heat days",
                    kind="line",
                    color=SERIES[4],
                    data=pct(hot, "hot", "the number of hot days (Tmax > 35 °C)"),
                ),
                c.Series(
                    key="drought",
                    name="Drought frequency",
                    kind="line",
                    color=NEGATIVE,
                    data=pct(dm, "drought", "the share of area in severe drought or worse (D2–D4)", none_base),
                ),
                c.Series(
                    key="r20",
                    name="Extreme rainfall events",
                    kind="line",
                    color=SERIES[1],
                    data=pct(r20, "r20", "the number of heavy-rain days (R20mm)"),
                ),
            ],
            as_of=f"{y1}-01-01",
            note="Mean temperature: change in °C on the right axis (a % change of a temperature is meaningless); the "
            "others: % change. A zero base year makes the % change undefined (null).",
            **self._prov(dt.date(y1, 12, 31), "tavg", "rain_annual", "hot_days", "spi_3", "r20mm", res="annual"),
        )

    # ---------------------------------------------------------------- forecast

    async def forecast(self, metric: str) -> dict[str, Any]:
        # The contract defines no climate forecast keys (the forecast page uses projections, which are unavailable).
        raise unknown("metric", metric, [])

    # ---------------------------------------------------------------- breakdown

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric in ("risk-index", "projection-bars"):
            raise unavailable("No climate risk index or projections in the database", metric)
        if metric == "seasonal-change":
            return (await self.bd_seasonal_change()).dump()
        if metric == "map-seasonality":
            y1 = await self.last_full_year()
            decades = [(2000, 2009), (2010, 2019), (2020, y1)]
            cols = [
                ("rain_annual", "Annual"),
                ("rain_season_djf", "DJF"),
                ("rain_season_mam", "MAM"),
                ("rain_season_jja", "JJA"),
                ("rain_season_son", "SON"),
            ]
            series = []
            for i, (var, name) in enumerate(cols):
                s = await self.annual_var(var, 2000, y1)
                series.append(
                    c.CategorySeries(
                        key=name.lower(),
                        name=name,
                        color=SERIES[i],
                        values=[rnd(s[(s.index >= a) & (s.index <= b)].mean(), 0) for a, b in decades],
                    )
                )
            return c.CategorySet(
                id="map-seasonality",
                title="Mean annual and seasonal precipitation by period",
                unit="mm",
                categories=[f"{a}–{b}" for a, b in decades],
                series=series,
                as_of=f"{y1}-01-01",
                **self._prov(dt.date(y1, 12, 31), "rain_annual", "rain_season_djf", res="annual"),
            ).dump()
        raise unknown("metric", metric, ["seasonal-change", "map-seasonality", "risk-index", "projection-bars"])

    async def bd_seasonal_change(self) -> c.CategorySet:
        """Seasonal mean temperature (°C) or mean seasonal rainfall total (mm) per period.

        A period is computed only when the scope's data cover all of it (`period_gap`); otherwise its values are null
        and the `unavailable` entry says why. Both come from the same check, so they cannot disagree.
        """
        var = self.ctx.select(SEASONAL_VAR)
        w = self.ctx.weights()
        if var == "temperature":
            last = await self.rain.latest_complete_month()
            t = weighted(await self.monthly_mean("tavg"), w).dropna(subset=["value"])
            t = t[t["date"] <= pd.Timestamp(last)]
            years = t["date"].dt.year
            end_year, variables, unit, nd = last.year, ["tavg"], "°C", 2
            res: Resolution = "monthly"
            as_of, prov_date = iso(last), last

            def season_value(a: int, b: int, months: tuple[int, ...], name: str) -> float | None:
                sub = t[(years >= a) & (years <= b) & t["date"].dt.month.isin(months)]
                return rnd(sub["value"].mean(), nd) if not sub.empty else None

            data_first, data_last = (int(years.min()), int(years.max())) if not t.empty else (None, None)
        else:
            end_year = await self.last_full_year()
            seasonal = {}
            for name in SEASONS:
                s = await self.annual_var(f"rain_season_{name.lower()}", HISTORY_START.year, end_year)
                seasonal[name] = s.dropna()
            have = [y for s in seasonal.values() for y in s.index]
            data_first, data_last = (int(min(have)), int(max(have))) if have else (None, None)
            end_year = min(end_year, data_last) if data_last else end_year
            variables, unit, nd = ["rain_season_djf"], "mm", 0
            res = "annual"
            as_of, prov_date = f"{end_year}-01-01", dt.date(end_year, 12, 31)

            def season_value(a: int, b: int, months: tuple[int, ...], name: str) -> float | None:
                s = seasonal[name]
                sub = s[(s.index >= a) & (s.index <= b)]
                return rnd(sub.mean(), nd) if not sub.empty else None

        periods = [("1981–2000", 1981, 2000), ("2001–2020", 2001, 2020), (f"2021–{end_year}", 2021, end_year)]
        series, unavailable = [], []
        for i, (label, a, b) in enumerate(periods):
            gap = period_gap(label, a, b, data_first, data_last)
            vals = [None if gap else season_value(a, b, months, name) for name, months in SEASONS.items()]
            if gap:
                unavailable.append(c.UnavailableField(field=f"series.p{i}", reason=gap))
            series.append(c.CategorySeries(key=f"p{i}", name=label, values=vals, color=SERIES[i]))
        extra: dict[str, Any] = {"unavailable": unavailable} if unavailable else {}
        return c.CategorySet(
            id="seasonal-change",
            title="Seasonal mean temperature by period" if var == "temperature" else "Mean seasonal rainfall by period",
            unit=unit,
            categories=list(SEASONS),
            series=series,
            as_of=as_of,
            **extra,
            **self._prov(prov_date, *variables, res=res),
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric in ("forecast-summary", "risk-outlook"):
            raise unavailable("No climate projections in the database", metric)
        handlers = {
            "statistics": self.tbl_statistics,
            "provinces": self.tbl_provinces,
            "signal-chain": self.tbl_signal_chain,
        }
        if metric not in handlers:
            raise unknown("metric", metric, [*handlers, "forecast-summary", "risk-outlook"])
        return (await handlers[metric](limit)).dump()

    async def tbl_statistics(self, limit: int | None) -> c.TableSet:
        y0, y1 = HISTORY_START.year, await self.last_full_year()
        yrs = range(y0, y1 + 1)
        tavg = await self.annual_agg(await self.annual_daily("tavg", "mean"), y0, y1)
        rain = await self.annual_var("rain_annual", y0, y1)
        hot = await self.annual_var("hot_days", y0, y1)
        ta, _, last = await self.temp_anomaly_12m()
        rows: list[dict[str, Any]] = []

        def add(rid: str, stat: str, value: str | None) -> None:
            rows.append({"id": rid, "statistic": stat, "value": value})

        tt, rt, ht = (sens_slope([s.get(y) for y in yrs]) for s in (tavg, rain, hot))
        add("tmean", f"Mean annual temperature {y0}–{y1}", f"{tavg.mean():.1f} °C" if not tavg.empty else None)
        add(
            "ttrend",
            "Warming trend (Sen's slope)",
            f"{tt.slope * 10:+.2f} °C/decade (p = {tt.p:.3f}, {tt.trend})" if tt else None,
        )
        add("tanom", f"Temperature anomaly, 12 months to {last:%b %Y}", f"{ta:+.2f} °C" if ta is not None else None)
        add("rmean", f"Mean annual rainfall {y0}–{y1}", f"{rain.mean():.0f} mm" if not rain.empty else None)
        add(
            "rtrend",
            "Rainfall trend (Sen's slope)",
            f"{rt.slope * 10:+.1f} mm/decade (p = {rt.p:.3f}, {rt.trend})" if rt else None,
        )
        add(
            "htrend",
            "Hot-day trend (Sen's slope)",
            f"{ht.slope * 10:+.1f} days/decade (p = {ht.p:.3f}, {ht.trend})" if ht else None,
        )
        if self.ctx.filters.scope.level == "municipality":
            # the DB's ENSO_Nino34_Correlation is withheld (its record includes 2026's Niño 3.4 of −41.28):
            # recomputed over the full years from the district's MAP and the annual Niño 3.4 mean
            code = self.ctx.filters.scope.id
            pair = (await self.data.annual("rain_annual")).merge(
                await self.data.annual("nino34_annual"), on=["district", "date"], suffixes=("_p", "_n")
            )
            pair = pair[(pair["district"] == code) & pair["date"].dt.year.between(y0, y1)].dropna()
            r = float(pair["value_p"].corr(pair["value_n"])) if len(pair) >= 8 else None
            if r is None:
                self.ctx.explain("rows.enso", f"A correlation needs 8 full years; this district has {len(pair)}")
            add("enso", f"Rainfall–Niño 3.4 correlation {y0}–{y1}", f"{r:+.2f}" if r is not None else None)
        return c.TableSet(
            id="statistics",
            title="Climate statistics",
            columns=[
                c.Column(key="statistic", label="Statistic", type="text"),
                c.Column(key="value", label="Value", type="text", align="right"),
            ],
            rows=rows,
            row_key="id",
            as_of=f"{y1}-01-01",
            **self._prov(
                dt.date(y1, 12, 31), "tavg", "rain_annual", "hot_days", "temp_anomaly", baseline=True, res="annual"
            ),
        )

    async def tbl_provinces(self, limit: int | None) -> c.TableSet:
        areas = self.ctx.state.spatial.district_areas()
        if areas is None:
            raise unavailable("Province aggregates need district boundaries", "district boundaries")
        y0, y1 = HISTORY_START.year, await self.last_full_year()
        yrs = range(y0, y1 + 1)
        _, ta_d, last = await self.temp_anomaly_12m()
        tavg = await self.annual_daily("tavg", "mean")
        rain, hot = await self.data.annual("rain_annual"), await self.data.annual("hot_days")
        groups = {
            districts.PROVINCES[p]: {d.code: areas[d.code] for d in districts.in_province(p)}
            for p in districts.PROVINCES
        }
        n = len(groups)
        for key, df, what in (
            ("temp", ta_d.dropna(), "a temperature anomaly"),
            (
                "rain",
                rain[rain["date"].dt.year == y1].dropna(subset=["value"]).set_index("district")["value"],
                "rainfall",
            ),
            (
                "hot",
                hot[hot["date"].dt.year == y1].dropna(subset=["value"]).set_index("district")["value"],
                "hot-day counts",
            ),
        ):
            if short := short_of_coverage(set(df.index), groups):
                self.ctx.explain(
                    f"rows.{key}", coverage_reason(f"{what} for {y1 if key != 'temp' else last.year}", short, n)
                )
        years_t: list[int] = []  # years with an area-weighted value, per province (trends need 8)
        years_h: list[int] = []
        rows = []
        for p in districts.PROVINCES:
            w = {d.code: areas[d.code] for d in districts.in_province(p)}

            def agg(df: pd.DataFrame, w: dict[str, float] = w) -> pd.Series:
                sub = df[(df["date"].dt.year >= y0) & (df["date"].dt.year <= y1)]
                a = weighted(sub, w)
                return a.set_index(a["date"].dt.year)["value"]

            t, r, h = agg(tavg), agg(rain), agg(hot)
            tt, ht = sens_slope([t.get(y) for y in yrs]), sens_slope([h.get(y) for y in yrs])
            years_t.append(int(t.notna().sum()))
            years_h.append(int(h.notna().sum()))
            rows.append(
                {
                    "id": p,
                    "province": districts.PROVINCES[p],
                    "temp": rnd(weighted_scalar(ta_d, w), 2),
                    "rain": rnd((r.get(y1) - r.mean()) / r.mean() * 100, 1) if y1 in r.index else None,
                    "trend": rnd(tt.slope * 10, 2) if tt else None,
                    "hot": rnd(h.get(y1), 1),
                    "hotTrend": (
                        "Rising"
                        if ht and ht.trend == "increasing"
                        else "Declining"
                        if ht and ht.trend == "decreasing"
                        else "Stable"
                    )
                    if ht
                    else None,
                }
            )
        for key, got, what in (("trend", years_t, "annual mean temperature"), ("hotTrend", years_h, "hot-day counts")):
            self.ctx.explain(
                f"rows.{key}",
                f"A trend needs at least 8 years of {what} with ≥ 95 % area coverage; in {y0}–{y1} the provinces "
                f"have at most {max(got, default=0)}",
            )
        return c.TableSet(
            id="provinces",
            title=f"Climate indicators by province (temperature: 12 months to {last:%b %Y}; "
            f"rainfall and hot days: {y1})",
            columns=[
                c.Column(key="province", label="Province", type="text"),
                c.Column(
                    key="temp",
                    label="Temp change",
                    type="delta",
                    unit="°C",
                    decimals=2,
                    scale="tempAnomaly",
                    good_direction="down",
                    align="right",
                ),
                c.Column(
                    key="rain",
                    label="Rainfall change",
                    type="delta",
                    unit="%",
                    decimals=0,
                    good_direction="up",
                    align="right",
                ),
                c.Column(
                    key="trend",
                    label="Trend",
                    type="number",
                    unit="°C/dec",
                    decimals=2,
                    scale="tempTrend",
                    align="right",
                ),
                c.Column(key="hot", label="Hot days", type="number", decimals=0, align="right"),
                c.Column(key="hotTrend", label="Trend", type="class", scale="trendClass"),
            ],
            rows=rows,
            row_key="id",
            total=len(rows),
            as_of=iso(last),
            **{
                **self._prov(last, "temp_anomaly", "tavg", "rain_annual", "hot_days", baseline=True, res="monthly"),
                "aggregation": "district-area-weighted",
            },
        )

    async def tbl_signal_chain(self, limit: int | None) -> c.TableSet:
        y0, y1 = HISTORY_START.year, await self.last_full_year()
        ta, _, last = await self.temp_anomaly_12m()
        et0 = await self.annual_agg(await self.annual_daily("et0_daily", "sum"), y0, y1)
        rain = await self.annual_var("rain_annual", y0, y1)
        q = await self.annual_agg(await self.annual_daily("runoff_depth_daily", "sum"), y0, y1)

        def pct(s: pd.Series) -> str | None:
            return f"{(s.get(y1) - s.mean()) / s.mean() * 100:+.0f} % ({y1} vs {y0}–{y1})" if y1 in s.index else None

        def signal(rid: str, label: str, metric: str, change: str | None) -> dict[str, Any]:
            # backend keys (signal, change, severity) + the contract's cascade keys (stage, metric, value, cls, scale,
            # bar); cls / bar / severity are null: no agreed thresholds for these signals
            return {
                "id": rid,
                "signal": label,
                "change": change,
                "severity": None,
                "stage": label,
                "metric": metric,
                "value": change,
                "cls": None,
                "scale": "risk",
                "bar": None,
            }

        rows: list[dict[str, Any]] = [
            signal("temp", "Temperature", "tmean", f"{ta:+.2f} °C (12 months)" if ta is not None else None),
            signal("et0", "Evaporative demand (ET₀)", "et0", pct(et0)),
            signal("rain", "Rainfall", "rainfall", pct(rain)),
            signal("runoff", "Runoff", "runoff", pct(q)),
        ]
        no_thresholds = "No agreed severity thresholds for these signals yet"
        return c.TableSet(
            id="signal-chain",
            title="Climate signal chain",
            columns=[
                c.Column(key="signal", label="Signal", type="text"),
                c.Column(key="change", label="Change", type="text"),
                c.Column(key="severity", label="Severity", type="class", scale="risk"),
                c.Column(key="stage", label="Signal", type="text"),
                c.Column(key="value", label="Change", type="text"),
                c.Column(key="cls", label="Severity", type="class", scale="risk"),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(last),
            unavailable=[
                c.UnavailableField(field=f"rows.{k}", reason=no_thresholds) for k in ("severity", "cls", "bar")
            ],
            **self._prov(
                last, "temp_anomaly", "et0_daily", "rain_annual", "runoff_depth_daily", baseline=True, res="monthly"
            ),
        )

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer in ("vulnerability", "proj-temp", "proj-rain", "proj-spi", "proj-availability"):
            raise unavailable("No climate projections or vulnerability index in the database", layer)
        w = self.ctx.weights()
        y0, y1 = HISTORY_START.year, await self.last_full_year()
        yrs = range(y0, y1 + 1)
        baseline = False
        if layer == "temp-trend":
            df = await self.annual_daily("tavg", "mean")
            df = df[df["date"].dt.year.between(y0, y1)]
            vals = {}
            for code, g in df.groupby("district"):
                s = g.set_index(g["date"].dt.year)["value"]
                tr = sens_slope([s.get(y) for y in yrs])
                vals[code] = rnd(tr.slope * 10, 3) if tr else None
                if gap := trend_gap([s.get(y) for y in yrs], "annual mean temperature"):
                    self.ctx.explain("values", gap)
            spec = (f"Temperature trend {y0}–{y1} (°C/decade)", "tempTrend", "°C/decade", dt.date(y1, 12, 31), ["tavg"])
        elif layer == "temp-anomaly":
            _, per, last = await self.temp_anomaly_12m()
            vals = {k: rnd(v, 2) for k, v in per.items()}
            spec = (f"Temperature anomaly, 12 months to {last:%b %Y} (°C)", "tempAnomaly", "°C", last, ["temp_anomaly"])
            baseline = True
        elif layer == "mean-temp":
            df = await self.annual_daily("tavg", "mean")
            vals = {
                k: rnd(v, 1)
                for k, v in df[df["date"].dt.year.between(y0, y1)].groupby("district")["value"].mean().items()
            }
            spec = (f"Mean annual temperature {y0}–{y1} (°C)", "temperature", "°C", dt.date(y1, 12, 31), ["tavg"])
        elif layer == "rain-trend":
            df = await self.data.annual("rain_annual")
            recent = df[df["date"].dt.year.between(y1 - 9, y1)].groupby("district")["value"].mean()
            early = df[df["date"].dt.year.between(y0, y1 - 10)].groupby("district")["value"].mean()
            vals = {k: rnd(v, 1) for k, v in ((recent - early) / early * 100).items()}
            spec = (
                f"Rainfall change, {y1 - 9}–{y1} vs {y0}–{y1 - 10} (%)",
                "anomalyPct",
                "%",
                dt.date(y1, 12, 31),
                ["rain_annual"],
            )
        elif layer in ("hot-days", "cdd"):
            var = "hot_days" if layer == "hot-days" else "cdd"
            df = await self.data.annual(var)
            vals = {k: rnd(v, 0) for k, v in df[df["date"].dt.year == y1].set_index("district")["value"].items()}
            label = "Hot days (Tmax > 35 °C)" if var == "hot_days" else "Consecutive dry days"
            spec = (f"{label}, {y1}", "days", "days", dt.date(y1, 12, 31), [var])
        elif layer == "et0":
            df = await self.annual_daily("et0_daily", "sum")
            vals = {
                k: rnd(v, 0)
                for k, v in df[df["date"].dt.year.between(y0, y1)].groupby("district")["value"].mean().items()
            }
            spec = (f"Reference ET₀ (mm/yr, {y0}–{y1} mean)", "rainfall", "mm", dt.date(y1, 12, 31), ["et0_daily"])
        else:
            raise unknown(
                "layer",
                layer,
                [
                    "temp-trend",
                    "temp-anomaly",
                    "mean-temp",
                    "rain-trend",
                    "hot-days",
                    "cdd",
                    "et0",
                    "vulnerability",
                    "proj-temp",
                    "proj-rain",
                    "proj-spi",
                    "proj-availability",
                ],
            )
        title, scale, unit, date, variables = spec
        map_res: Resolution = "monthly" if layer == "temp-anomaly" else "annual"
        prov = self.ctx.provenance(date, self.src(*variables), baseline=baseline, res=map_res)
        prov["aggregation"] = "district"
        return c.MapLayer(
            id=layer,
            title=title,
            kind="choropleth",
            level="municipality",
            scale=scale,
            unit=unit,
            date=iso(date) or "",
            values={k: v for k, v in vals.items() if k in w},
            geometry="districts",
            **prov,
        ).dump()


async def prewarm(data: DataAccess) -> None:
    for var in ("tavg", "tmax", "tmin", "et0_daily", "pet_daily", "aet_daily", "runoff_depth_daily"):
        await data.daily_monthly_stats(var, ())
    for var in (
        "hot_days",
        "r20mm",
        "cdd",
        "cwd",
        "rx1day",
        "rx5day",
        "rain_annual",
        "rain_season_djf",
        "rain_season_mam",
        "rain_season_jja",
        "rain_season_son",
    ):
        await data.annual(var)
    await data.monthly("temp_anomaly")
