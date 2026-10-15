"""Runoff Status Monitoring (contract module `runoff`, spec §7; DB Module 5).

Source: `runoff_variables_master` (modelled, daily, complete calendar). Every district was modelled with the same
6 500 km² area, so (decision D2) the API recovers runoff **depth** (mm = model volume / 6 500 km²) and re-derives
volumes and flows from the **real district area**:

    volume (Mm³) = depth (mm) × area (km²) / 1000        flow (m³/s) = depth × area × 1000 / 86 400

Withheld (D5): the table's own volumes, peak flows, catchment area and constants. Rainfall from `6.1`. There are no
gauging stations: station counts, station maps and the by-province station breakdown are unavailable; districts serve
as entities (D1).

Methods (docs/DATA_MAPPING.md §5): depths area-weighted; flows and volumes summed over the scope (WMA: district volume
apportioned by the share of its area inside the WMA); normals = same calendar month 2000–(last full year); SSI computed
with app.core.indices (gamma fit per calendar month on the scope's monthly runoff); flood frequency = Log-Pearson III
on annual maxima of derived daily mean flow (not instantaneous peaks) with a parametric-bootstrap 90 % band.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from app.core import districts
from app.core.aggregate import sum_fractions, weighted, weighted_scalar, window_normal
from app.core.data import DataAccess
from app.core.errors import DataUnavailable, NotFound
from app.core.indices import standardized_index
from app.core.palettes import NORMAL, OBSERVED, SERIES, YEAR_COLOURS, drought_bands
from app.core.provenance import iso
from app.core.stats import rnd, sens_slope
from app.core.thresholds import drought_class, drought_label
from app.modules.common import Ctx, Selector, forecast_unavailable, unknown
from app.modules.drivers import driver_series
from app.modules.rainfall.service import HISTORY_START, MONTHS, RainfallService, _months
from app.schemas import contract as c

MODULE = "runoff"
Q = "runoff_depth_daily"
RETURN_PERIODS = [1.25, 2, 5, 10, 25, 50, 100]
NO_STATIONS = "No gauging-station data in the database (runoff is modelled per district municipality)"
DRIVER_VAR = Selector("var", ("rain", "aet", "sm"), "rain")
TOP_ORDER = Selector("order", ("highest", "lowest"), "highest")
SELECTORS: dict[tuple[str, str], tuple[Selector, ...]] = {
    ("timeseries", "drivers"): (DRIVER_VAR,),
    ("table", "top-stations"): (TOP_ORDER,),
}


def _d(ts: Any) -> dt.date:
    d: dt.date = pd.Timestamp(ts).date()
    return d


class RunoffService:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.data: DataAccess = ctx.data
        self.rain = RainfallService(ctx)

    # ---------------------------------------------------------------- helpers

    def src(self, *variables: str) -> list[str]:
        return self.rain.src(*variables)

    def _prov(self, as_of: dt.date | None, *variables: str, baseline: str | None = None) -> dict[str, Any]:
        p = self.ctx.provenance(as_of, self.src(*variables))
        p.pop("asOf")
        if baseline:
            p["baseline"], p["baselineNote"] = baseline, self.ctx.state.registry.baseline.note
        return p

    def kpi_extra(self, as_of: dt.date | None, *variables: str) -> dict[str, Any]:
        return {"as_of": iso(as_of), "source": self.src(*variables), "is_stale": self.ctx.stale(as_of)}

    async def newest(self) -> dt.date:
        return await self.data.newest(Q)

    async def last_full_year(self) -> int:
        n = await self.newest()
        return n.year if n >= dt.date(n.year, 12, 31) else n.year - 1

    async def latest_complete_month(self) -> dt.date:
        n = await self.newest()
        last = n.replace(day=calendar.monthrange(n.year, n.month)[1])
        return n.replace(day=1) if last <= n else _d(pd.Timestamp(n.replace(day=1)) - pd.DateOffset(months=1))

    def areas(self) -> dict[str, float]:
        a = self.ctx.state.spatial.district_areas()
        if a is None:
            raise DataUnavailable("Runoff volumes need district areas (boundaries)", missing=["district boundaries"])
        return a

    def flow_factors(self) -> dict[str, float]:
        """m³/s per mm/day for each district in scope (area × 1000 / 86 400, × share of area in a WMA)."""
        a = self.areas()
        return {
            k: a[k] * 1000 / 86400 * f for k, f in sum_fractions(self.ctx.filters.scope, self.ctx.state.spatial).items()
        }

    def volume_factors(self) -> dict[str, float]:
        """Mm³ per mm for each district in scope (area / 1000, × share of area in a WMA)."""
        a = self.areas()
        return {k: a[k] / 1000 * f for k, f in sum_fractions(self.ctx.filters.scope, self.ctx.state.spatial).items()}

    async def monthly_depth(self) -> pd.DataFrame:
        """District monthly runoff depth (mm) with day counts."""
        dm = await self.data.daily_monthly_stats(Q, ())
        return dm.rename(columns={"sum": "value"})[["district", "date", "value", "days"]]

    async def scope_monthly_depth(self, complete_only: bool = True) -> pd.Series:
        s = weighted(await self.monthly_depth(), self.ctx.weights()).set_index("date")["value"]
        if complete_only:
            s = s[s.index <= pd.Timestamp(await self.latest_complete_month())]
        return s

    async def scope_monthly_volume(self) -> pd.Series:
        f = self.volume_factors()
        dm = await self.monthly_depth()
        dm = dm[dm["district"].isin(f)]
        n_need = len(f)
        g = (
            dm.assign(v=dm["value"] * dm["district"].map(f))
            .groupby("date")
            .agg(v=("v", "sum"), n=("district", "nunique"))
        )
        return g.loc[g["n"] == n_need, "v"]

    async def normals(self, y1: int) -> pd.DataFrame:
        """District monthly depth normal: same calendar month, full months 2000–y1. Columns district, month, normal."""
        dm = await self.monthly_depth()
        dm = dm[dm["date"].dt.year.between(HISTORY_START.year, y1)]
        dm = dm[dm["days"] >= dm["date"].dt.days_in_month]
        return (
            dm.assign(month=dm["date"].dt.month)
            .groupby(["district", "month"], as_index=False)["value"]
            .mean()
            .rename(columns={"value": "normal"})
        )

    async def window_depth(self, start: dt.date, end: dt.date) -> pd.Series:
        g = await self.data.daily_window_stats(Q, start, end)
        return g["sum"].where(g["days"] >= 0.95 * ((end - start).days + 1))

    async def daily_flow(self, start: dt.date, end: dt.date, *, required: bool = True) -> pd.Series:
        """Scope daily flow (m³/s); an empty window is a 501 unless `required=False` (the caller reports it)."""
        flow = await self.data.daily_scope_sum(Q, start, end, self.flow_factors())
        if flow.empty and required:
            raise DataUnavailable(
                f"No day between {start} and {end} has runoff for every district in this scope "
                "(a scope-wide daily flow needs all of them)",
                missing=["runoff_depth_daily"],
            )
        return flow

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        w = self.ctx.weights()
        start, end = await self.rain.window()
        y1 = await self.last_full_year()
        base = f"{HISTORY_START.year}–{y1}"
        kpis: list[c.Kpi] = []
        depth = await self.window_depth(start, end)
        normal = window_normal(await self.normals(y1), start, end)
        q, n = weighted_scalar(depth, w), weighted_scalar(normal, w)
        spark = (await self.scope_monthly_depth()).tail(12)
        kpis.append(
            c.Kpi(
                id="runoff",
                metric="runoff",
                label="Annual Runoff",
                sublabel=f"{start:%d %b %Y} – {end:%d %b %Y}",
                value=rnd(q, 1),
                unit="mm",
                decimals=0,
                spark=[v for v in (rnd(x, 1) for x in spark) if v is not None],
                **self.kpi_extra(end, Q),
            )
        )
        kpis.append(
            c.Kpi(
                id="anomaly",
                metric="runoffAnomaly",
                label="Runoff Anomaly",
                sublabel=f"vs {base} normal",
                value=rnd((q - n) / n * 100, 1) if q is not None and n else None,
                unit="%",
                decimals=0,
                **self.kpi_extra(end, Q),
            )
        )
        reason_peak = "Peak flows are withheld: the table scales them to a constant 6 500 km² area for every district"
        kpis.append(
            c.Kpi(
                id="peak",
                metric="peakFlow",
                label="Peak Flow (Qmax)",
                value=None,
                unit="m³/s",
                unavailable_reason=reason_peak,
            )
        )
        bf = await self.data.daily_window_stats("baseflow_component", start, end)
        dws = await self.data.daily_window_stats(Q, start, end)
        model_area = self.ctx.state.registry.datasets["runoff_variables_master"].model_area_km2 or 6500.0
        bfi = (bf["sum"] * 86400) / (dws["sum"] * model_area * 1000)  # both scaled by the same model area: ratio holds
        kpis.append(
            c.Kpi(
                id="bfi",
                metric="baseflowIndex",
                label="Baseflow Index",
                sublabel="baseflow / total flow, 12 months",
                value=rnd(weighted_scalar(bfi, w), 2),
                decimals=2,
                **self.kpi_extra(end, "baseflow_component", Q),
            )
        )
        rain = await self.data.daily_window_stats("rain_daily", start, end)
        coef = dws["sum"] / rain["sum"]
        kpis.append(
            c.Kpi(
                id="coefficient",
                metric="runoffCoefficient",
                label="Runoff Coefficient",
                sublabel="Σ runoff / Σ rainfall, 12 months",
                value=rnd(weighted_scalar(coef, w), 3),
                decimals=2,
                **self.kpi_extra(end, Q, "rain_daily"),
            )
        )
        ssi = standardized_index(await self.scope_monthly_depth(), 6, (HISTORY_START.year, y1))
        lm = await self.latest_complete_month()
        sv = ssi.get(pd.Timestamp(lm))
        sv = None if sv is None or np.isnan(sv) else float(sv)
        cls = drought_class(sv)
        kpis.append(
            c.Kpi(
                id="ssi6",
                metric="ssi",
                label="SSI-6",
                sublabel=f"{lm:%b %Y}, computed (gamma, {base})",
                value=rnd(sv, 2),
                decimals=2,
                status=c.Status(scale="drought", id=cls, label=drought_label(cls)),
                **self.kpi_extra(lm, Q),
            )
        )
        for kid, metric, label in (
            ("normal", "stationsHighFlow", "Stations with Normal Flow"),
            ("low", "stationsLowFlow", "Stations with Low Flow"),
        ):
            kpis.append(c.Kpi(id=kid, metric=metric, label=label, value=None, unit="%", unavailable_reason=NO_STATIONS))
        as_of = max(dt.date.fromisoformat(k.as_of) for k in kpis if k.as_of)
        body = c.KpiSet(
            module=MODULE,
            as_of=iso(as_of) or "",
            scope=self.ctx.scope,
            kpis=kpis,
            components=[
                c.Component(key=k.id, as_of=k.as_of, source=k.source or [], is_stale=bool(k.is_stale))
                for k in kpis
                if k.as_of
            ],
            unavailable=[
                c.UnavailableField(field="kpis.peak", reason=reason_peak),
                c.UnavailableField(field="kpis.normal", reason=NO_STATIONS),
                c.UnavailableField(field="kpis.low", reason=NO_STATIONS),
            ],
            **self._prov(as_of, Q, "baseflow_component", "rain_daily", baseline=f"{base} same-month mean"),
        )
        return body.dump()

    # ---------------------------------------------------------------- time series

    async def timeseries(self, metric: str) -> dict[str, Any]:
        handlers = {
            "discharge": self.ts_discharge,
            "runoff-anomaly": self.ts_anomaly,
            "ssi": self.ts_ssi,
            "rainfall-runoff": self.ts_rainfall_runoff,
            "water-balance": self.ts_water_balance,
            "flood-frequency": self.ts_flood_frequency,
            "fdc": self.ts_fdc,
            "seasonal": self.ts_seasonal,
            "national-volume": self.ts_national_volume,
            "drivers": self.ts_drivers,
        }
        if metric not in handlers:
            raise unknown("metric", metric, list(handlers))
        return (await handlers[metric]()).dump()

    async def _window_months(self) -> list[pd.Timestamp]:
        start, _ = await self.rain.window()
        last = await self.latest_complete_month()
        return _months(min(start.replace(day=1), last), last)

    async def ts_discharge(self) -> c.SeriesSet:
        start, end = await self.rain.window()
        y1 = await self.last_full_year()
        flow = await self.daily_flow(start, end)
        monthly = flow.groupby(flow.index.to_period("M")).mean()
        lt = await self.daily_flow(HISTORY_START, dt.date(y1, 12, 31))
        lt_m = lt.groupby(lt.index.month).mean()
        downsample = len(flow) > self.ctx.state.settings.max_series_points
        daily_pts = (
            [[iso(p.start_time.date()), rnd(v, 2)] for p, v in monthly.items()]
            if downsample
            else [[iso(_d(t)), rnd(v, 2)] for t, v in flow.items()]
        )
        return c.SeriesSet(
            id="discharge",
            title="Discharge (derived)",
            x_type="time",
            axes=[c.Axis(unit="m³/s", label="Discharge")],
            series=[
                c.Series(
                    key="daily",
                    name="Daily flow" + (" (monthly means — downsampled)" if downsample else ""),
                    kind="line",
                    color=SERIES[0],
                    data=daily_pts,
                ),
                c.Series(
                    key="monthly",
                    name="Monthly mean",
                    kind="step",
                    color=OBSERVED,
                    data=[[iso(p.start_time.date()), rnd(v, 2)] for p, v in monthly.items()],
                ),
                c.Series(
                    key="normal",
                    name=f"Long-term mean ({HISTORY_START.year}–{y1})",
                    kind="line",
                    color=NORMAL,
                    dash=True,
                    data=[[iso(p.start_time.date()), rnd(lt_m.get(p.month), 2)] for p in monthly.index],
                ),
            ],
            as_of=iso(end),
            note="Derived flow = modelled runoff depth × real district area / 86 400 s, summed over the scope (not "
            "gauged). Series over 2 000 points are downsampled to monthly means.",
            **self._prov(end, Q, baseline=f"{HISTORY_START.year}–{y1} same-month mean"),
        )

    async def ts_anomaly(self) -> c.SeriesSet:
        y1 = await self.last_full_year()
        s = await self.scope_monthly_depth()
        clim = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)]
        normal = clim.groupby(clim.index.month).mean()
        an = pd.Series(
            {
                t: (v - normal.get(t.month)) / normal.get(t.month) * 100 if normal.get(t.month) else np.nan
                for t, v in s.items()
            }
        )
        ma = an.rolling(12, min_periods=12).mean()
        ms = await self._window_months()
        last = ms[-1].date()
        return c.SeriesSet(
            id="runoff-anomaly",
            title="Runoff anomaly",
            x_type="time",
            axes=[c.Axis(unit="%", label="Anomaly")],
            series=[
                c.Series(
                    key="monthly",
                    name="Monthly anomaly",
                    kind="bar",
                    color=SERIES[0],
                    data=[[iso(m.date()), rnd(an.get(m), 1)] for m in ms],
                ),
                c.Series(
                    key="ma12",
                    name="12-month moving average",
                    kind="line",
                    color=SERIES[3],
                    data=[[iso(m.date()), rnd(ma.get(m), 1)] for m in ms],
                ),
            ],
            as_of=iso(last),
            **self._prov(last, Q, baseline=f"{HISTORY_START.year}–{y1} same-month mean"),
        )

    async def ts_ssi(self) -> c.SeriesSet:
        y1 = await self.last_full_year()
        s = await self.scope_monthly_depth()
        ms = await self._window_months()
        series = []
        for i, k in enumerate((1, 3, 6, 12)):
            idx = standardized_index(s, k, (HISTORY_START.year, y1))
            series.append(
                c.Series(
                    key=f"ssi{k}",
                    name=f"SSI-{k}",
                    kind="line",
                    color=SERIES[i],
                    data=[[iso(m.date()), rnd(idx.get(m), 2)] for m in ms],
                )
            )
        last = ms[-1].date()
        return c.SeriesSet(
            id="ssi",
            title="Standardised Streamflow Index",
            x_type="time",
            axes=[c.Axis(unit="—", label="SSI")],
            series=series,
            bands=[c.Band.model_validate(b) for b in drought_bands()],
            as_of=iso(last),
            note=f"Computed by the API from the scope's monthly runoff depth (gamma fit per calendar month, "
            f"{HISTORY_START.year}–{y1}); the DB's SSI is withheld.",
            **self._prov(last, Q, baseline=f"{HISTORY_START.year}–{y1} calibration"),
        )

    async def ts_rainfall_runoff(self) -> c.SeriesSet:
        rain = await self.rain.ts_monthly_series()
        s = await self.scope_monthly_depth()
        ms = await self._window_months()
        last = ms[-1].date()
        return c.SeriesSet(
            id="rainfall-runoff",
            title="Rainfall and runoff",
            x_type="time",
            axes=[c.Axis(unit="mm", label="Rainfall"), c.Axis(unit="mm", label="Runoff")],
            series=[
                c.Series(key="rain", name="Monthly rainfall", kind="bar", axis=0, color=SERIES[0], data=rain["rain"]),
                c.Series(
                    key="runoff",
                    name="Monthly runoff",
                    kind="line",
                    axis=1,
                    color=SERIES[3],
                    data=[[iso(m.date()), rnd(s.get(m), 1)] for m in ms],
                ),
            ],
            as_of=iso(last),
            **self._prov(last, "rain_daily", Q),
        )

    async def ts_water_balance(self) -> c.SeriesSet:
        w = self.ctx.weights()
        ms = await self._window_months()
        p = weighted((await self.rain.monthly_stats()).rename(columns={"sum": "value"}), w).set_index("date")["value"]
        adm = await self.data.daily_monthly_stats("aet_daily", ())
        aet = weighted(adm.rename(columns={"sum": "value"}), w).set_index("date")["value"]
        q = await self.scope_monthly_depth()
        last = ms[-1].date()
        return c.SeriesSet(
            id="water-balance",
            title="Water balance",
            x_type="time",
            axes=[c.Axis(unit="mm", label="Depth")],
            series=[
                c.Series(
                    key="p",
                    name="Rainfall (P)",
                    kind="bar",
                    color=SERIES[0],
                    data=[[iso(m.date()), rnd(p.get(m), 1)] for m in ms],
                ),
                c.Series(
                    key="aet",
                    name="Actual ET (AET)",
                    kind="line",
                    color=SERIES[1],
                    data=[[iso(m.date()), rnd(aet.get(m), 1)] for m in ms],
                ),
                c.Series(
                    key="q",
                    name="Runoff (Q)",
                    kind="line",
                    color=SERIES[3],
                    data=[[iso(m.date()), rnd(q.get(m), 1)] for m in ms],
                ),
            ],
            as_of=iso(last),
            **self._prov(last, "rain_daily", "aet_daily", Q),
        )

    async def ts_flood_frequency(self) -> c.SeriesSet:
        y1 = await self.last_full_year()
        flow = await self.daily_flow(HISTORY_START, dt.date(y1, 12, 31))
        amax = flow.groupby(flow.index.year).max().dropna()
        amax = amax[amax > 0]
        if len(amax) < 10:
            raise DataUnavailable("Flood frequency needs at least 10 annual maxima", missing=["annual maxima"])
        logq = np.log10(amax.to_numpy())
        mu, sd, g = float(logq.mean()), float(logq.std(ddof=1)), float(stats.skew(logq, bias=False))

        def quantiles(m: float, s: float, sk: float) -> np.ndarray:
            k = stats.pearson3.ppf([1 - 1 / t for t in RETURN_PERIODS], sk)
            return np.asarray(10 ** (m + k * s))

        fit = quantiles(mu, sd, g)
        rng = np.random.default_rng(0)
        boot = []
        for _ in range(500):
            sample = stats.pearson3.rvs(g, loc=mu, scale=sd, size=len(logq), random_state=rng)
            boot.append(
                quantiles(float(sample.mean()), float(sample.std(ddof=1)), float(stats.skew(sample, bias=False)))
            )
        lo, hi = np.percentile(np.array(boot), [5, 95], axis=0)
        ranked = amax.sort_values(ascending=False).to_numpy()
        obs_t = [(len(ranked) + 1) / (i + 1) for i in range(len(ranked))]
        return c.SeriesSet(
            id="flood-frequency",
            title="Flood frequency (Log-Pearson III)",
            x_type="value",
            x_unit="years",
            x_log=True,
            axes=[c.Axis(unit="m³/s", label="Peak flow", log=True)],
            series=[
                c.Series(
                    key="ci",
                    name="90% confidence",
                    kind="band",
                    color="#C6DBEF",
                    data=[[t, rnd(a, 1), rnd(b, 1)] for t, a, b in zip(RETURN_PERIODS, lo, hi, strict=True)],
                ),
                c.Series(
                    key="obs",
                    name="Observed annual maxima",
                    kind="scatter",
                    color=OBSERVED,
                    data=[[rnd(t, 2), rnd(v, 1)] for t, v in zip(obs_t, ranked, strict=True)],
                ),
                c.Series(
                    key="fit",
                    name="Log-Pearson III fit",
                    kind="line",
                    color=SERIES[3],
                    data=[[t, rnd(v, 1)] for t, v in zip(RETURN_PERIODS, fit, strict=True)],
                ),
            ],
            as_of=f"{y1}-01-01",
            note=f"Annual maxima of derived daily mean flow {HISTORY_START.year}–{y1} "
            "(not instantaneous peaks; modelled runoff). Weibull plotting positions; "
            "90 % band from a 500-sample parametric bootstrap.",
            **self._prov(dt.date(y1, 12, 31), Q),
        )

    async def ts_fdc(self) -> c.SeriesSet:
        newest = await self.newest()
        y_ago = _d(pd.Timestamp(newest) - pd.DateOffset(years=1))
        curves = [
            ("last", "Last 12 months", y_ago + dt.timedelta(days=1), newest),
            (
                "prev",
                "Previous 12 months",
                _d(pd.Timestamp(y_ago) - pd.DateOffset(years=1)) + dt.timedelta(days=1),
                y_ago,
            ),
            ("lt", "Long-term (2011–2020)", dt.date(2011, 1, 1), dt.date(2020, 12, 31)),
        ]
        pcts = np.linspace(0, 100, 101)
        series, unavailable = [], []
        for i, (key, name, a, b) in enumerate(curves):
            flow = (await self.daily_flow(a, b, required=False)).to_numpy()
            vals = np.percentile(flow, 100 - pcts) if len(flow) else [None] * len(pcts)
            if not len(flow):
                reason = f"No day between {a} and {b} has runoff for every district in this scope"
                unavailable.append(c.UnavailableField(field=f"series.{key}", reason=reason))
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
        return c.SeriesSet(
            id="fdc",
            title="Flow-duration curves",
            x_type="value",
            x_unit="% of time exceeded",
            axes=[c.Axis(unit="m³/s", label="Discharge", log=True)],
            series=series,
            as_of=iso(newest),
            note="Derived daily flow (modelled depth × real district area).",
            **({"unavailable": unavailable} if unavailable else {}),
            **self._prov(newest, Q),
        )

    async def _by_calendar_month(
        self, s: pd.Series, how: str, sid: str, title: str, unit: str, label: str, y1: int
    ) -> c.SeriesSet:
        newest = await self.newest()
        complete_last = pd.Timestamp(await self.latest_complete_month())
        s = s[s.index <= complete_last]
        lt = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)].groupby(lambda t: t.month).mean()
        series = []
        for i, yr in enumerate((newest.year, newest.year - 1)):
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
        last = complete_last.date()
        return c.SeriesSet(
            id=sid,
            title=title,
            x_type="category",
            axes=[c.Axis(unit=unit, label=label)],
            series=series,
            as_of=iso(last),
            note=f"Complete months only; long-term = {HISTORY_START.year}–{y1}. Derived from modelled runoff × real "
            f"district area ({how}).",
            **self._prov(last, Q, baseline=f"{HISTORY_START.year}–{y1}"),
        )

    async def ts_seasonal(self) -> c.SeriesSet:
        y1 = await self.last_full_year()
        flow = await self.daily_flow(HISTORY_START, await self.newest())
        monthly = flow.groupby(flow.index.to_period("M")).mean()
        monthly.index = monthly.index.to_timestamp()
        return await self._by_calendar_month(
            monthly, "mean flow", "seasonal", "Average flow by month", "m³/s", "Average flow", y1
        )

    async def ts_national_volume(self) -> c.SeriesSet:
        y1 = await self.last_full_year()
        return await self._by_calendar_month(
            await self.scope_monthly_volume(),
            "volume",
            "national-volume",
            "Runoff volume by month",
            "Mm³",
            "Runoff volume",
            y1,
        )

    async def ts_drivers(self) -> c.SeriesSet:
        return await driver_series(self.ctx, self.rain, self.ctx.select(DRIVER_VAR))

    # ---------------------------------------------------------------- forecast / breakdown

    async def forecast(self, metric: str) -> dict[str, Any]:
        allowed = ["runoff", "flow", "flood-probability"]
        if metric not in allowed:
            raise unknown("metric", metric, allowed)
        raise forecast_unavailable(MODULE, metric)

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric == "by-province":
            raise DataUnavailable(f"Stations by flow status: {NO_STATIONS}", missing=["gauging stations"])
        if metric != "by-wma":
            raise unknown("metric", metric, ["by-province", "by-wma"])
        sp = self.ctx.state.spatial
        ov, areas = sp.wma_overlap(), sp.district_areas()
        if ov is None or areas is None:
            raise DataUnavailable("WMA volumes need district boundaries", missing=["district boundaries"])
        start, end = await self.rain.window()
        y1 = await self.last_full_year()
        depth = await self.window_depth(start, end)
        normal = window_normal(await self.normals(y1), start, end)
        cats, cur, lt = [], [], []
        for wma in sorted(sp.wmas, key=lambda r: int(r.id[3:])):
            f = {k: a / 1000 for k, a in ov[wma.id].items()}  # overlap km² / 1000: Mm³ per mm
            cats.append(wma.name)
            cur.append(rnd(sum(depth.get(k, np.nan) * v for k, v in f.items()), 0))
            lt.append(rnd(sum(normal.get(k, np.nan) * v for k, v in f.items()), 0))
        return c.CategorySet(
            id="by-wma",
            title=f"Runoff volume by WMA, {start:%d %b %Y} – {end:%d %b %Y}",
            unit="Mm³",
            categories=cats,
            series=[
                c.CategorySeries(key="current", name="Last 12 months", values=cur, color=SERIES[0]),
                c.CategorySeries(key="lt", name="Long-term mean", values=lt, color=NORMAL),
            ],
            as_of=iso(end),
            **{
                **self._prov(end, Q, baseline=f"{HISTORY_START.year}–{y1} same-period mean"),
                "aggregation": "district-overlap",
            },
        ).dump()

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        handlers = {"statistics": self.tbl_statistics, "top-stations": self.tbl_top, "events": self.tbl_events}
        if metric not in handlers:
            raise unknown("metric", metric, list(handlers))
        return (await handlers[metric](limit)).dump()

    async def tbl_statistics(self, limit: int | None) -> c.TableSet:
        w = self.ctx.weights()
        y1 = await self.last_full_year()
        yrs = range(HISTORY_START.year, y1 + 1)
        start, end = await self.rain.window()
        mdepth = await self.monthly_depth()
        q_ann = weighted(
            mdepth.assign(date=mdepth["date"].dt.to_period("Y").dt.start_time)
            .groupby(["district", "date"], as_index=False)["value"]
            .sum(),
            w,
        )
        q_ann = q_ann.set_index(q_ann["date"].dt.year)["value"]
        rain_m = (await self.rain.monthly_stats()).rename(columns={"sum": "value"})
        p_ann = weighted(
            rain_m.assign(date=rain_m["date"].dt.to_period("Y").dt.start_time)
            .groupby(["district", "date"], as_index=False)["value"]
            .sum(),
            w,
        )
        p_ann = p_ann.set_index(p_ann["date"].dt.year)["value"]
        coef_ann = q_ann / p_ann
        cur_q = weighted_scalar(await self.window_depth(start, end), w)
        cur_p = weighted_scalar((await self.data.daily_window_stats("rain_daily", start, end))["sum"], w)
        rows = []
        for rid, name, unit, cur, ann, nd in (
            ("runoff", "Runoff", "mm", cur_q, q_ann, 1),
            (
                "coefficient",
                "Runoff coefficient",
                "—",
                cur_q / cur_p if cur_q is not None and cur_p else None,
                coef_ann,
                3,
            ),
        ):
            clim = ann[(ann.index >= HISTORY_START.year) & (ann.index <= y1)].mean()
            tr = sens_slope([ann.get(y) for y in yrs])
            rows.append(
                {
                    "id": rid,
                    "variable": f"{name} ({unit})",
                    "current": rnd(cur, nd),
                    "climatology": rnd(clim, nd),
                    "anomaly": rnd(cur - clim, nd) if cur is not None else None,
                    "trend": rnd(tr.slope * 10, nd + 1) if tr else None,
                }
            )
        return c.TableSet(
            id="statistics",
            title=f"Runoff statistics, 12 months to {end:%d %b %Y}",
            columns=[
                c.Column(key="variable", label="Variable", type="text"),
                c.Column(key="current", label="Current (12 m)", type="number", align="right"),
                c.Column(
                    key="climatology", label=f"Climatology ({HISTORY_START.year}–{y1})", type="number", align="right"
                ),
                c.Column(key="anomaly", label="Anomaly", type="delta", good_direction="up", align="right"),
                c.Column(key="trend", label="Trend (Sen's slope)", type="number", unit="/decade", align="right"),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(end),
            **self._prov(end, Q, "rain_daily", baseline=f"{HISTORY_START.year}–{y1} annual mean"),
        )

    async def tbl_top(self, limit: int | None) -> c.TableSet:
        """Districts (D1: stand-ins for stations) by mean derived flow in the latest complete month."""
        order = self.ctx.select(TOP_ORDER)
        sel = self.ctx.filters.scope
        a = self.areas()
        lm = pd.Timestamp(await self.latest_complete_month())
        dm = await self.monthly_depth()
        rows = []
        for code in sel.district_codes:
            cur = dm[(dm["district"] == code) & (dm["date"] == lm)]
            prev = dm[(dm["district"] == code) & (dm["date"] == lm - pd.DateOffset(years=1))]
            if cur.empty:
                continue
            f = a[code] * 1000 / 86400
            flow = float(cur["value"].iloc[0]) / float(cur["days"].iloc[0]) * f
            pflow = float(prev["value"].iloc[0]) / float(prev["days"].iloc[0]) * f if not prev.empty else None
            d = districts.by_code(code)
            assert d is not None
            rows.append(
                {
                    "id": code,
                    "station": d.official_name,
                    "code": code,
                    "flow": rnd(flow, 1),
                    "vsLastYear": rnd(flow - pflow, 1) if pflow is not None else None,
                }
            )
        rows.sort(key=lambda r: float(r["flow"] or 0), reverse=order == "highest")
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return c.TableSet(
            id="top-stations",
            title=f"{order.capitalize()}-flow districts, {lm:%b %Y} (derived mean flow)",
            columns=[
                c.Column(key="rank", label="#", type="rank", align="center"),
                c.Column(key="station", label="Station", type="text"),
                c.Column(key="code", label="Code", type="text"),
                c.Column(key="flow", label="Flow", type="number", unit="m³/s", decimals=1, scale="flow", align="right"),
                c.Column(
                    key="vsLastYear",
                    label="vs last year",
                    type="delta",
                    unit="m³/s",
                    decimals=1,
                    good_direction="up",
                    align="right",
                ),
            ],
            rows=rows[: limit or 10],
            row_key="id",
            link_to="/runoff?entityId=",
            total=len(rows),
            as_of=iso(lm.date()),
            **{**self._prov(lm.date(), Q), "aggregation": "district"},
        )

    async def tbl_events(self, limit: int | None) -> c.TableSet:
        """Days in the window where a district's derived flow exceeded its long-term Q10 (high-flow events)."""
        sel = self.ctx.filters.scope
        start, end = await self.rain.window()
        y1 = await self.last_full_year()
        a = self.areas()
        # Q10 = flow exceeded 10 % of the time = 90th percentile of daily depth (flow ∝ depth for a district)
        q10 = await self.data.daily_percentile(Q, HISTORY_START, dt.date(y1, 12, 31), 0.9)
        high = await self.data.daily_exceedances(
            Q, start, end, {k: v for k, v in q10.items() if k in sel.district_codes}
        )
        rows = []
        for _, r in high.iterrows():
            code = str(r["district"])
            d = districts.by_code(code)
            assert d is not None
            rows.append(
                {
                    "id": f"{code}-{_d(r['date'])}",
                    "date": iso(_d(r["date"])),
                    "station": d.official_name,
                    "event": "High flow (> Q10)",
                    "flow": rnd(float(r["value"]) * a[code] * 1000 / 86400, 1),
                    "severity": None,
                }
            )
        rows.sort(key=lambda r: -float(r["flow"] or 0))
        return c.TableSet(
            id="events",
            title=f"High-flow days, {start:%d %b %Y} – {end:%d %b %Y}",
            columns=[
                c.Column(key="date", label="Date", type="date"),
                c.Column(key="station", label="Station", type="text"),
                c.Column(key="event", label="Event", type="text"),
                c.Column(key="flow", label="Flow", type="number", unit="m³/s", decimals=1, align="right"),
                c.Column(key="severity", label="Severity", type="class", scale="severity"),
            ],
            rows=rows[: limit or 20],
            row_key="id",
            total=len(rows),
            as_of=iso(end),
            unavailable=[
                c.UnavailableField(
                    field="rows.severity", reason="No flow-event severity thresholds defined (none in thresholds.ts)"
                )
            ],
            **{
                **self._prov(end, Q, baseline=f"Q10 of daily flow {HISTORY_START.year}–{y1}"),
                "aggregation": "district",
            },
        )

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "catchment-runoff":
            raise DataUnavailable(
                "Catchment runoff is not available: data are per district", missing=["catchment-level data"]
            )
        if layer in ("stations", "flow-status", "flow-anomaly", "ssi"):
            raise DataUnavailable(
                f"Station map layers need gauging stations with coordinates: {NO_STATIONS}",
                missing=["gauging stations"],
            )
        w = self.ctx.weights()
        start, end = await self.rain.window()
        y1 = await self.last_full_year()
        newest = await self.newest()
        baseline = None
        if layer == "annual-runoff":
            vals, title, scale, unit, date = (
                await self.window_depth(start, end),
                "Annual runoff, last 12 months (mm)",
                "runoff",
                "mm",
                end,
            )
        elif layer == "runoff-anomaly":
            d = await self.window_depth(start, end)
            n = window_normal(await self.normals(y1), start, end)
            vals, title, scale, unit, date = (d - n) / n * 100, "Runoff anomaly, 12 months (%)", "anomalyPct", "%", end
            baseline = f"{HISTORY_START.year}–{y1} same-period mean"
        elif layer == "monthly-runoff":
            dm = await self.monthly_depth()
            m = pd.Timestamp(newest.replace(day=1))
            vals = dm[dm["date"] == m].set_index("district")["value"]
            title = f"Monthly runoff – {newest:%b %Y} (mm)" + (
                "" if newest.day == m.days_in_month else f" to {newest:%d %b}"
            )
            scale, unit, date = "runoff", "mm", newest
        elif layer == "runoff-coefficient":
            dm = await self.monthly_depth()
            rm = await self.rain.monthly_stats()
            q = dm[dm["date"].dt.year.between(HISTORY_START.year, y1)].groupby("district")["value"].sum()
            p = rm[rm["date"].dt.year.between(HISTORY_START.year, y1)].groupby("district")["sum"].sum()
            vals, title, scale, unit, date = (
                q / p,
                f"Runoff coefficient ({HISTORY_START.year}–{y1})",
                "runoffCoeff",
                "—",
                dt.date(y1, 12, 31),
            )
        else:
            raise unknown(
                "layer",
                layer,
                [
                    "annual-runoff",
                    "runoff-anomaly",
                    "monthly-runoff",
                    "runoff-coefficient",
                    "catchment-runoff",
                    "stations",
                    "flow-status",
                    "flow-anomaly",
                    "ssi",
                ],
            )
        nd = 3 if layer == "runoff-coefficient" else 1
        prov = self.ctx.provenance(date, self.src(Q, *(["rain_daily"] if layer == "runoff-coefficient" else [])))
        prov["aggregation"] = "district"
        if baseline:
            prov["baseline"], prov["baselineNote"] = baseline, self.ctx.state.registry.baseline.note
        return c.MapLayer(
            id=layer,
            title=title,
            kind="choropleth",
            level="municipality",
            scale=scale,
            unit=unit,
            date=iso(date) or "",
            values={str(k): rnd(v, nd) for k, v in vals.items() if k in w},
            geometry="districts",
            **prov,
        ).dump()

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return await self.rain.entities()

    async def entity(self, code: str) -> dict[str, Any]:
        d = districts.by_code(code)
        if d is None:
            raise NotFound(f"Unknown entity {code!r}")
        a = self.areas()
        newest = await self.newest()
        start = _d(pd.Timestamp(newest) - pd.DateOffset(years=1)) + dt.timedelta(days=1)
        y1 = await self.last_full_year()
        depth = (await self.window_depth(start, newest)).get(d.code)
        normal = window_normal(await self.normals(y1), start, newest).get(d.code)
        lm = pd.Timestamp(await self.latest_complete_month())
        dm = await self.monthly_depth()
        cur = dm[(dm["district"] == d.code) & (dm["date"] == lm)]
        flow = (
            float(cur["value"].iloc[0]) / float(cur["days"].iloc[0]) * a[d.code] * 1000 / 86400
            if not cur.empty
            else None
        )
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            c.EntityField(label="Area", value=round(a[d.code]), unit="km²"),
            c.EntityField(label="Runoff, last 12 months", value=rnd(depth, 0), unit="mm", as_of=iso(newest)),
            c.EntityField(
                label="Runoff anomaly, last 12 months",
                value=rnd((depth - normal) / normal * 100, 0) if depth is not None and normal else None,
                unit="%",
                scale="anomalyPct",
                as_of=iso(newest),
            ),
            c.EntityField(
                label="Runoff volume, last 12 months",
                value=rnd(depth * a[d.code] / 1000, 0) if depth else None,
                unit="Mm³",
                as_of=iso(newest),
            ),
            c.EntityField(
                label=f"Mean flow, {lm:%b %Y}", value=rnd(flow, 1), unit="m³/s", scale="flow", as_of=iso(lm.date())
            ),
        ]
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type="district",
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code} · modelled runoff",
            title=d.official_name,
            fields=fields,
            image="river",
            as_of=iso(newest),
            source=self.src(Q),
        ).dump()


async def prewarm(data: DataAccess) -> None:
    for var in (Q, "aet_daily", "baseflow_component"):
        await data.daily_monthly_stats(var, ())
    newest = await data.newest(Q)
    y1 = newest.year if newest >= dt.date(newest.year, 12, 31) else newest.year - 1
    await data.daily_percentile(Q, HISTORY_START, dt.date(y1, 12, 31), 0.9)  # Q10 thresholds for the events table
