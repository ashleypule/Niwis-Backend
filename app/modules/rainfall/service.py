"""Rainfall Status Monitoring (contract module `rainfall`, spec §6; DB Module 4).

Sources (canonical, app/registry/datasets.yaml): daily rainfall and monthly totals/anomalies/rainy days from
`6.1_core_rainfall_variables_master`; SPI from `6.2_…`; SPEI from `meteorological_drought_master`; annual indices
from `4_2_wet_dry_spells_master` / `4_3_mean_annual_precipitation_master`.

Methods (docs/DATA_MAPPING.md §1):
- Totals over a window are sums of daily rainfall; normals come from the DB climatology (value − stored anomaly,
  2000–2026), pro-rated by the days of each month inside the window. Anomaly % = (Σw·P − Σw·N) / Σw·N.
- Monthly indices (SPI, SPEI, monthly anomaly) use complete months only: the current month is partial (the DB's
  partial-month SPI and anomaly compare a few days with a full-month normal).
- Province / national / WMA values are area-weighted from districts (app.core.aggregate).
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

import pandas as pd

from app.core import districts
from app.core.aggregate import (
    coverage_reason,
    is_complete_month,
    monthly_normals,
    short_of_coverage,
    weighted,
    weighted_scalar,
    window_normal,
)
from app.core.data import DataAccess
from app.core.errors import BadRequest, DataUnavailable, NotFound
from app.core.filters import parse_window
from app.core.palettes import NEGATIVE, NORMAL, POSITIVE, SERIES, YEAR_COLOURS, drought_bands
from app.core.provenance import Resolution, iso
from app.core.stats import rnd, sens_slope
from app.core.thresholds import drought_class_or_none
from app.forecasting import pages as fc
from app.modules.common import Ctx, Selector, anomaly_split_reasons, drought_status, unknown
from app.registry.catalogue import get_catalogue
from app.schemas import contract as c

MODULE = "rainfall"
MONTHS = [calendar.month_abbr[i] for i in range(1, 13)]
SPI_SCALES = (1, 3, 6, 12)
HISTORY_START = dt.date(2000, 1, 1)
EVENT_MM = 50.01  # "> 50 mm/day": the data have 2 decimals, so ≥ 50.01 ≡ > 50
CDD_LOOKBACK_DAYS = 730
DRY_DAY_MM = 1.0
WINDOW = Selector("window", ("1", "3", "6", "12"), "1")  # months summed before comparing with the normal
EXTREMES_KIND = Selector("kind", ("wettest", "driest"), "wettest")
EVENTS_KIND = Selector("kind", ("count", "max"), "count")
SELECTORS: dict[tuple[str, str], tuple[Selector, ...]] = {
    ("timeseries", "anomaly"): (WINDOW,),
    ("table", "extremes"): (EXTREMES_KIND,),
    ("breakdown", "events"): (EVENTS_KIND,),
}


# entity datasets: `entityId` selects the district they describe (the station series requires it)
ENTITY_DATASETS = {("timeseries", "station")}


def _d(ts: Any) -> dt.date:
    d: dt.date = pd.Timestamp(ts).date()
    return d


def _months(start: dt.date, end: dt.date) -> list[pd.Timestamp]:
    return list(pd.date_range(start.replace(day=1), end.replace(day=1), freq="MS"))


FORECAST_SPI_SCALE = Selector("scale", ("1", "3"), "3")  # forecast SPI (page forecasts: SPI-1 and SPI-3)


class RainfallService:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.data = ctx.data

    # ---------------------------------------------------------------- shared building blocks

    async def newest_daily(self) -> dt.date:
        return await self.data.newest("rain_daily")

    async def latest_complete_month(self) -> dt.date:
        newest = await self.newest_daily()
        m = newest.replace(day=1)
        return m if is_complete_month(m, newest) else (pd.Timestamp(m) - pd.offsets.MonthBegin(1)).date()

    async def window(self) -> tuple[dt.date, dt.date]:
        newest = await self.newest_daily()
        return self.ctx.filters.window.resolve(newest, HISTORY_START)

    async def normals(self) -> pd.DataFrame:
        return monthly_normals(await self.data.monthly("rain_monthly"), await self.data.monthly("rain_anomaly_mm"))

    async def window_totals(self, start: dt.date, end: dt.date) -> pd.Series:
        """Rainfall per district over [start, end] from daily values; NaN where < 95 % of days are present."""
        g = await self.data.daily_window_stats("rain_daily", start, end, (20.0,))
        n_days = (end - start).days + 1
        return g["sum"].where(g["days"] >= 0.95 * n_days)

    async def monthly_stats(self) -> pd.DataFrame:
        return await self.data.daily_monthly_stats("rain_daily", (DRY_DAY_MM, 20.0, EVENT_MM))

    def src(self, *variables: str) -> list[str]:
        return sorted({self.data.table_of(v) for v in variables})

    def kpi_extra(self, as_of: dt.date | None, sources: list[str], res: Resolution = "daily") -> dict[str, Any]:
        return {"as_of": iso(as_of), "source": sources, **self.ctx.stamp(as_of, res)}

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        w = self.ctx.weights()
        start, end = await self.window()
        newest = await self.newest_daily()
        kpis: list[c.Kpi] = []
        rain_src = self.src("rain_daily", "rain_anomaly_mm")
        window_label = f"{start:%d %b %Y} – {end:%d %b %Y}"

        totals = await self.window_totals(start, end)
        normal = window_normal(await self.normals(), start, end)
        p, n = weighted_scalar(totals, w), weighted_scalar(normal, w)
        prev = await self.window_totals(_shift_year(start), _shift_year(end))
        p_prev = weighted_scalar(prev, w)
        spark = await self._monthly_aggregate_totals(w, months=12)
        kpis.append(
            c.Kpi(
                id="total",
                metric="rainfall",
                label="Total Rainfall",
                sublabel=window_label,
                value=rnd(p, 1),
                unit="mm",
                decimals=0,
                delta=rnd(p - p_prev, 1) if p is not None and p_prev is not None else None,
                delta_unit=" mm",
                delta_label="vs same period last year",
                spark=[v for v in spark if v is not None],
                **self.kpi_extra(end, rain_src),
            )
        )
        anomaly = (p - n) / n * 100 if p is not None and n else None
        kpis.append(
            c.Kpi(
                id="anomaly",
                metric="rainfallAnomaly",
                label="Rainfall Anomaly",
                sublabel=f"vs 2000–2026 normal ({rnd(n, 0):g} mm)" if n else "vs 2000–2026 normal",
                value=rnd(anomaly, 1),
                unit="%",
                decimals=0,
                **self.kpi_extra(end, rain_src),
            )
        )

        ws = await self.data.daily_window_stats("rain_daily", start, end, (20.0,))
        scoped = ws[ws.index.isin(w)]
        r20 = scoped["ge_20"]
        kpis.append(
            c.Kpi(
                id="r20",
                metric="heavyRainDays",
                label="Heavy Rain Days (R20mm)",
                sublabel="days ≥ 20 mm, area-weighted district mean" if len(w) > 1 else "days ≥ 20 mm",
                value=rnd(weighted_scalar(r20, w), 1),
                unit="days",
                decimals=0,
                **self.kpi_extra(end, self.src("rain_daily")),
            )
        )
        if scoped["max"].notna().any():
            code = str(scoped["max"].idxmax())
            top = scoped.loc[code]
            dname = districts.by_code(code)
            kpis.append(
                c.Kpi(
                    id="maxDaily",
                    metric="maxDailyRain",
                    label="Max Daily Rainfall",
                    sublabel=f"{dname.official_name if dname else code}, {_d(top['max_date']):%d %b %Y}",
                    value=rnd(float(top["max"]), 1),
                    unit="mm",
                    decimals=0,
                    **self.kpi_extra(end, self.src("rain_daily")),
                )
            )
        cdd = await self.current_dry_spell(newest)
        kpis.append(
            c.Kpi(
                id="cdd",
                metric="cdd",
                label="Consecutive Dry Days",
                sublabel=f"current spell of days < {DRY_DAY_MM:g} mm to {newest:%d %b}",
                value=rnd(weighted_scalar(cdd, w), 0),
                unit="days",
                decimals=0,
                **self.kpi_extra(newest, self.src("rain_daily")),
            )
        )
        month = await self.latest_complete_month()
        for kid, var, metric, label in (("spi6", "spi_6", "spi", "SPI-6"), ("spei6", "spei_6", "spei", "SPEI-6")):
            v = await self._index_at(var, month, w)
            if v is None:
                self.ctx.explain(f"kpis.{kid}", f"No area-weighted {label} for this scope in {month:%b %Y}")
            kpis.append(
                c.Kpi(
                    id=kid,
                    metric=metric,
                    label=label,
                    sublabel=f"{month:%b %Y} (latest complete month)",
                    value=rnd(v, 2),
                    decimals=2,
                    **drought_status(v),
                    **self.kpi_extra(month, self.src(var), res="monthly"),
                )
            )
        reason = "No rainfall-station data in the database (data are per district municipality)"
        kpis.append(
            c.Kpi(
                id="stations",
                metric="activeStations",
                label="Active Rainfall Stations",
                value=None,
                unavailable_reason=reason,
            )
        )
        as_of = max(_d(x.as_of) for x in kpis if x.as_of)
        components = [
            c.Component(
                key=k.id,
                as_of=k.as_of,
                source=k.source or [],
                is_stale=bool(k.is_stale),
                **({"period_end": k.period_end} if k.period_end else {}),
            )
            for k in kpis
            if k.as_of
        ]
        body = c.KpiSet(
            module=MODULE,
            as_of=iso(as_of) or "",
            scope=self.ctx.scope,
            kpis=kpis,
            components=components,
            unavailable=[c.UnavailableField(field="kpis.stations", reason=reason)],
            **{
                k: v
                for k, v in self.ctx.provenance(as_of, rain_src + self.src("spi_6", "spei_6"), baseline=True).items()
                if k != "asOf"
            },
        )
        return body.dump()

    async def current_dry_spell(self, newest: dt.date) -> pd.Series:
        """Days since the last day with ≥ 1 mm, per district (capped at the look-back window)."""
        start = newest - dt.timedelta(days=CDD_LOOKBACK_DAYS - 1)
        ws = await self.data.daily_window_stats("rain_daily", start, newest, (DRY_DAY_MM,))
        last_wet = ws[f"last_ge_{DRY_DAY_MM:g}"]
        return ((pd.Timestamp(newest) - last_wet).dt.days.astype(float)).fillna(float(CDD_LOOKBACK_DAYS))

    async def _index_at(self, var: str, month: dt.date, w: dict[str, float]) -> float | None:
        df = await self.data.monthly(var)
        at = df[df["date"] == pd.Timestamp(month)].set_index("district")["value"]
        return weighted_scalar(at, w)

    async def _monthly_aggregate_totals(self, w: dict[str, float], months: int) -> list[float | None]:
        last = await self.latest_complete_month()
        ms = await self.monthly_stats()
        agg = weighted(ms.rename(columns={"sum": "value"}), w)
        agg = agg[agg["date"] <= pd.Timestamp(last)].sort_values("date").tail(months)
        return [rnd(v, 1) for v in agg["value"]]

    # ---------------------------------------------------------------- time series

    async def timeseries(self, metric: str) -> dict[str, Any]:
        handlers = {
            "monthly": self.ts_monthly,
            "anomaly": self.ts_anomaly,
            "spi-spei": self.ts_spi_spei,
            "intensity": self.ts_intensity,
            "dry-wet": self.ts_dry_wet,
            "station": self.ts_station,
        }
        if metric not in handlers:
            raise unknown("metric", metric, list(handlers))
        return (await handlers[metric]()).dump()

    async def _complete_months_in_window(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        start, _ = await self.window()
        last = await self.latest_complete_month()
        first = pd.Timestamp(start.replace(day=1))
        if first > pd.Timestamp(last):
            first = pd.Timestamp(last)
        return first, pd.Timestamp(last)

    async def ts_monthly(self) -> c.SeriesSet:
        w = self.ctx.weights()
        newest = await self.newest_daily()
        ms = await self.monthly_stats()
        agg = weighted(ms.rename(columns={"sum": "value"}), w)
        series = []
        for i, year in enumerate((newest.year, newest.year - 1, newest.year - 2)):
            rows = agg[agg["date"].dt.year == year].set_index(agg[agg["date"].dt.year == year]["date"].dt.month)
            data = [[MONTHS[m - 1], rnd(rows["value"].get(m), 1) if m in rows.index else None] for m in range(1, 13)]
            series.append(c.Series(key=str(year), name=str(year), kind="bar", color=YEAR_COLOURS[i], data=data))
        norm = (await self.normals()).rename(columns={"normal": "value"})
        nagg = weighted(norm, w, by="month")
        nmap = dict(zip(nagg["month"], nagg["value"], strict=True))
        series.append(
            c.Series(
                key="normal",
                name="2000–2026 mean",
                kind="line",
                color=NORMAL,
                dash=True,
                data=[[MONTHS[m - 1], rnd(nmap.get(m), 1)] for m in range(1, 13)],
            )
        )
        partial = "" if is_complete_month(newest.replace(day=1), newest) else f" {newest:%b %Y} is to {newest:%d %b}."
        return c.SeriesSet(
            id="monthly",
            title="Monthly rainfall",
            x_type="category",
            axes=[c.Axis(unit="mm", label="Rainfall")],
            series=series,
            as_of=iso(newest),
            note=f"Normal: 2000–2026 mean (data start in 2000).{partial}",
            **self._prov(newest, "rain_daily", "rain_anomaly_mm", baseline=True),
        )

    async def ts_monthly_series(self) -> dict[str, list[list[Any]]]:
        """Scope monthly rainfall and its 2000–2026 normal for the complete months of the window."""
        w = self.ctx.weights()
        first, last = await self._complete_months_in_window()
        ms = (await self.monthly_stats()).rename(columns={"sum": "value"})
        p = weighted(ms, w).set_index("date")["value"]
        norm = (await self.normals()).rename(columns={"normal": "value"})
        n = weighted(norm, w, by="month").set_index("month")["value"]
        months = _months(first.date(), last.date())
        return {
            "rain": [[iso(m.date()), rnd(p.get(m), 1)] for m in months],
            "normal": [[iso(m.date()), rnd(n.get(m.month), 1)] for m in months],
        }

    async def ts_anomaly(self) -> c.SeriesSet:
        """Anomaly of the rolling `window`-month total (1 = each month) vs the sum of the same months' normals."""
        n_months = int(self.ctx.select(WINDOW))
        w = self.ctx.weights()
        first, last = await self._complete_months_in_window()
        ms = (await self.monthly_stats()).rename(columns={"sum": "value"})
        p = weighted(ms, w).set_index("date")["value"]
        norm = await self.normals()
        normal = {
            mo: weighted_scalar(norm[norm["month"] == mo].set_index("district")["normal"], w) for mo in range(1, 13)
        }
        rows = []
        for m in _months(first.date(), last.date()):
            span = [m - pd.DateOffset(months=k) for k in range(n_months)]
            pv = [p.get(x) for x in span]
            nv = [normal[x.month] for x in span]
            ok = all(v is not None and not pd.isna(v) for v in pv) and all(v for v in nv)
            rows.append((m, (sum(pv) - sum(nv)) / sum(nv) * 100 if ok else None))  # type: ignore[arg-type]
        anomaly_split_reasons(self.ctx, [a for _, a in rows], "rainfall anomaly")
        pos = [[iso(m), rnd(a, 1) if a is not None and a >= 0 else None] for m, a in rows]
        neg = [[iso(m), rnd(a, 1) if a is not None and a < 0 else None] for m, a in rows]
        return c.SeriesSet(
            id="anomaly",
            title="Monthly rainfall anomaly" if n_months == 1 else f"{n_months}-month rainfall anomaly",
            x_type="time",
            axes=[c.Axis(unit="%", label="Anomaly")],
            series=[
                c.Series(key="pos", name="Positive anomaly", kind="bar", color=POSITIVE, stack="a", data=pos),
                c.Series(key="neg", name="Negative anomaly", kind="bar", color=NEGATIVE, stack="a", data=neg),
            ],
            as_of=iso(last),
            note=(
                "Complete months only; anomaly vs the 2000–2026 monthly mean."
                if n_months == 1
                else f"Total of the {n_months} complete months ending each month vs the sum of their 2000–2026 "
                "monthly means."
            ),
            **self._prov(last.date(), "rain_daily", "rain_anomaly_mm", baseline=True, res="monthly"),
        )

    async def _index_series(self, var: str, first: pd.Timestamp, last: pd.Timestamp) -> list[list[Any]]:
        w = self.ctx.weights()
        df = await self.data.monthly(var)
        df = df[(df["date"] >= first) & (df["date"] <= last)]
        agg = weighted(df, w).sort_values("date")
        return [[iso(_d(t)), rnd(v, 2)] for t, v in zip(agg["date"], agg["value"], strict=True)]

    async def ts_spi_spei(self) -> c.SeriesSet:
        first, last = await self._complete_months_in_window()
        specs = [(f"spi_{k}", f"SPI-{k}") for k in SPI_SCALES] + [("spei_6", "SPEI-6"), ("spei_12", "SPEI-12")]
        series = [
            c.Series(
                key=var.replace("_", ""),
                name=name,
                **self.ctx.col_source(var),
                kind="line",
                color=SERIES[i % len(SERIES)],
                dash=var.startswith("spei"),
                data=await self._index_series(var, first, last),
            )
            for i, (var, name) in enumerate(specs)
        ]
        return c.SeriesSet(
            id="spi-spei",
            title="SPI / SPEI",
            x_type="time",
            axes=[c.Axis(unit="—", label="Index")],
            series=series,
            bands=[c.Band.model_validate(b) for b in drought_bands()],
            ref_lines=[c.RefLine(y=0, label="Normal")],
            as_of=iso(last),
            note="Area-weighted mean of district indices (approximation of a regional SPI). Complete months only.",
            **self._prov(last.date(), *[v for v, _ in specs], res="monthly"),
        )

    async def ts_intensity(self) -> c.SeriesSet:
        w = self.ctx.weights()
        first, last = await self._complete_months_in_window()
        ms = await self.monthly_stats()
        ms = ms[(ms["date"] >= first) & (ms["date"] <= last)]
        scoped = ms[ms["district"].isin(w)]
        mx = scoped.groupby("date")["max"].max()
        r20 = weighted(ms.rename(columns={"ge_20": "value"}), w).set_index("date")["value"]
        dates = _months(first.date(), last.date())
        return c.SeriesSet(
            id="intensity",
            title="Rainfall intensity",
            x_type="time",
            axes=[c.Axis(unit="mm", label="Daily max rainfall"), c.Axis(unit="days", label="R20mm days")],
            series=[
                c.Series(
                    key="max",
                    name="Daily max (mm)",
                    kind="bar",
                    axis=0,
                    color=SERIES[0],
                    data=[[iso(m), rnd(mx.get(m), 1)] for m in dates],
                ),
                c.Series(
                    key="r20",
                    name="R20mm days (district mean)",
                    kind="line",
                    axis=1,
                    color=SERIES[3],
                    data=[[iso(m), rnd(r20.get(m), 2)] for m in dates],
                ),
            ],
            as_of=iso(last),
            note="Daily max = highest district daily total in the scope; R20mm days = area-weighted district mean.",
            **self._prov(last.date(), "rain_daily", res="monthly"),
        )

    async def _annual_window(self) -> tuple[int, int]:
        """Full years for annual charts: the whole record unless `from`/`to` are given."""
        newest = await self.newest_daily()
        last_full = newest.year - 1 if newest < dt.date(newest.year, 12, 31) else newest.year
        win = self.ctx.filters.window
        y0 = win.date_from.year if win.date_from else HISTORY_START.year
        y1 = min(win.date_to.year, last_full) if win.date_to else last_full
        return y0, y1

    async def _annual_agg(self, var: str, y0: int, y1: int) -> pd.Series:
        df = await self.data.annual(var)
        df = df[(df["date"].dt.year >= y0) & (df["date"].dt.year <= y1)]
        agg = weighted(df, self.ctx.weights())
        return agg.set_index(agg["date"].dt.year)["value"]

    async def ts_dry_wet(self) -> c.SeriesSet:
        y0, y1 = await self._annual_window()
        cdd, cwd = await self._annual_agg("cdd", y0, y1), await self._annual_agg("cwd", y0, y1)
        years = range(y0, y1 + 1)
        return c.SeriesSet(
            id="dry-wet",
            title="Dry and wet spells",
            x_type="time",
            axes=[c.Axis(unit="days", label="Spell length")],
            series=[
                c.Series(
                    key="cdd",
                    name="CDD (dry days)",
                    **self.ctx.col_source("cdd"),
                    kind="bar",
                    color=NEGATIVE,
                    data=[[f"{y}-01-01", rnd(cdd.get(y), 1)] for y in years],
                ),
                c.Series(
                    key="cwd",
                    name="CWD (wet days)",
                    **self.ctx.col_source("cwd"),
                    kind="bar",
                    color=POSITIVE,
                    data=[[f"{y}-01-01", rnd(cwd.get(y), 1)] for y in years],
                ),
            ],
            as_of=f"{y1}-01-01",
            note="Annual ETCCDI indices, full years only (area-weighted district mean).",
            **self._prov(dt.date(y1, 12, 31), "cdd", "cwd", res="annual"),
        )

    async def ts_station(self) -> c.SeriesSet:
        code = self.ctx.filters.entity_id
        if not code:
            raise BadRequest(
                "`entityId` (district MDB code) is required for metric=station", issues=[{"param": "entityId"}]
            )
        d = districts.by_code(code)
        if d is None:
            raise NotFound(f"Unknown entity {code!r}")
        first, last = await self._complete_months_in_window()
        ms = await self.monthly_stats()
        mine = ms[(ms["district"] == d.code) & (ms["date"] >= first) & (ms["date"] <= last)].set_index("date")["sum"]
        spi = {}
        for var in ("spi_3", "spi_12"):
            df = await self.data.monthly(var)
            spi[var] = df[df["district"] == d.code].set_index("date")["value"]
        dates = _months(first.date(), last.date())
        return c.SeriesSet(
            id="station",
            title=f"{d.official_name}: rainfall and SPI",
            x_type="time",
            axes=[c.Axis(unit="mm", label="Rainfall"), c.Axis(unit="—", label="Index")],
            series=[
                c.Series(
                    key="rain",
                    name="Rainfall",
                    kind="bar",
                    axis=0,
                    color=SERIES[0],
                    data=[[iso(m), rnd(mine.get(m), 1)] for m in dates],
                ),
                c.Series(
                    key="spi3",
                    name="SPI-3",
                    **self.ctx.col_source("spi_3"),
                    kind="line",
                    axis=1,
                    color=SERIES[2],
                    data=[[iso(m), rnd(spi["spi_3"].get(m), 2)] for m in dates],
                ),
                c.Series(
                    key="spi12",
                    name="SPI-12",
                    **self.ctx.col_source("spi_12"),
                    kind="line",
                    axis=1,
                    color=SERIES[3],
                    data=[[iso(m), rnd(spi["spi_12"].get(m), 2)] for m in dates],
                ),
            ],
            as_of=iso(last),
            note="District values (NIWIS data are per district municipality; no stations).",
            **{**self._prov(last.date(), "rain_daily", "spi_3", "spi_12", res="monthly"), "aggregation": "district"},
        )

    def _prov(
        self, as_of: dt.date, *variables: str, baseline: bool = False, res: Resolution = "daily"
    ) -> dict[str, Any]:
        p = self.ctx.provenance(as_of, self.src(*variables), baseline=baseline, res=res)
        p.pop("asOf")
        return p

    # ---------------------------------------------------------------- forecast

    async def forecast(self, metric: str) -> dict[str, Any]:
        """Precomputed statistical forecasts (app.forecasting.pages): monthly rainfall and SPI-1 / SPI-3 (`scale`)."""
        if metric not in ("rainfall", "spi"):
            raise unknown("metric", metric, ["rainfall", "spi"])
        if metric == "rainfall":
            return fc.series_set(self.ctx, "rainfall", set_id="rainfall-forecast", title="Monthly rainfall forecast")
        k = self.ctx.select(FORECAST_SPI_SCALE)
        return fc.series_set(self.ctx, f"spi-{k}", set_id="spi-forecast", title=f"SPI-{k} forecast")

    # ---------------------------------------------------------------- breakdown

    async def breakdown(self, metric: str) -> dict[str, Any]:
        w = self.ctx.weights()
        _, y1 = await self._annual_window()
        if metric == "seasonal":
            years = list(range(y1 - 6, y1 + 1))
            # the rainfall table's own seasons (6.1, spec §6.1): totals of the 3 calendar months in the same year;
            # the climate page serves 4_3's DJF / MAM / JJA / SON (B11)
            seasons = [("jfm", "JFM"), ("amj", "AMJ"), ("jas", "JAS"), ("ond", "OND")]
            series = []
            for i, (s, name) in enumerate(seasons):
                agg = await self._annual_agg(f"rain_season_{s}", years[0], years[-1])
                series.append(
                    c.CategorySeries(key=s, name=name, color=SERIES[i], values=[rnd(agg.get(y), 1) for y in years])
                )
            return c.CategorySet(
                id="seasonal",
                title="Seasonal rainfall (JFM, AMJ, JAS, OND)",
                unit="mm",
                categories=[str(y) for y in years],
                series=series,
                as_of=f"{y1}-01-01",
                **self._prov(dt.date(y1, 12, 31), "rain_season_jfm", "rain_season_ond", res="annual"),
            ).dump()
        if metric == "events":
            kind = self.ctx.select(EVENTS_KIND)
            newest = await self.newest_daily()
            years = list(range(newest.year - 9, newest.year + 1))
            ms = await self.monthly_stats()
            col = f"ge_{EVENT_MM:g}"
            scoped = ms[ms["district"].isin(w)]
            by_year = scoped.groupby(scoped["date"].dt.year)
            if kind == "count":
                vals, unit, name = by_year[col].sum(), "events", "Events > 50 mm/day (all districts)"
                title = f"Heavy rainfall events ({years[-1]} to {newest:%d %b})"
            else:
                vals, unit, name = by_year["max"].max(), "mm", "Highest daily rainfall (any district)"
                title = f"Highest daily rainfall per year ({years[-1]} to {newest:%d %b})"
            return c.CategorySet(
                id="events",
                title=title,
                unit=unit,
                categories=[str(y) for y in years],
                series=[
                    c.CategorySeries(
                        key="events" if kind == "count" else "max",
                        name=name,
                        color=SERIES[0],
                        values=[rnd(vals.get(y), 0 if kind == "count" else 1) for y in years],
                    )
                ],
                as_of=iso(newest),
                **{**self._prov(newest, "rain_daily"), "aggregation": "district"},
            ).dump()
        raise unknown("metric", metric, ["seasonal", "events"])

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        handlers = {
            "provinces": self.tbl_provinces,
            "extremes": self.tbl_extremes,
            "statistics": self.tbl_statistics,
            "sources": self.tbl_sources,
        }
        if metric not in handlers:
            raise unknown("metric", metric, list(handlers))
        return (await handlers[metric](limit)).dump()

    async def tbl_provinces(self, limit: int | None) -> c.TableSet:
        start, end = await self.window()
        areas = self.ctx.state.spatial.district_areas()
        if areas is None:
            raise DataUnavailable("Province aggregates need district boundaries", missing=["district boundaries"])
        totals = await self.window_totals(start, end)
        normal = window_normal(await self.normals(), start, end)
        month = await self.latest_complete_month()
        spi = await self.data.monthly("spi_12")
        spi_at = spi[spi["date"] == pd.Timestamp(month)].set_index("district")["value"]
        provs = list(districts.PROVINCES)
        sel = self.ctx.filters.scope
        if sel.level in ("province", "municipality"):
            p = sel.id if sel.level == "province" else districts.by_code(sel.id).province  # type: ignore[union-attr]
            provs = [p]
        groups = {districts.PROVINCES[p]: {d.code: areas[d.code] for d in districts.in_province(p)} for p in provs}
        if short := short_of_coverage(set(totals.dropna().index), groups):
            reason = coverage_reason(f"rainfall on ≥ 95 % of the days {start} – {end}", short, len(groups))
            self.ctx.explain("rows.total", reason)
            self.ctx.explain("rows.anomaly", reason)
        if short := short_of_coverage(set(spi_at.dropna().index), groups):
            self.ctx.explain("rows.status", coverage_reason(f"SPI-12 for {month:%b %Y}", short, len(groups)))
        rows = []
        for p in provs:
            w = {d.code: areas[d.code] for d in districts.in_province(p)}
            t, n = weighted_scalar(totals, w), weighted_scalar(normal, w)
            cls = drought_class_or_none(weighted_scalar(spi_at, w))
            rows.append(
                {
                    "id": p,
                    "province": districts.PROVINCES[p],
                    "total": rnd(t, 1),
                    "anomaly": rnd((t - n) / n * 100, 1) if t is not None and n else None,
                    "status": cls,
                }
            )
        rows.sort(key=lambda r: -float(r["total"]) if r["total"] is not None else float("inf"))
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return c.TableSet(
            id="provinces",
            title=f"Rainfall by province, {start:%d %b %Y} – {end:%d %b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank", align="center"),
                c.Column(key="province", label="Province", type="text"),
                c.Column(key="total", label="Total (12 m)", type="number", unit="mm", decimals=0, align="right"),
                c.Column(
                    key="anomaly",
                    label="Anomaly",
                    type="delta",
                    unit="%",
                    decimals=0,
                    good_direction="up",
                    align="right",
                ),
                c.Column(key="status", label="Status", type="class", scale="drought"),
            ],
            rows=rows[:limit] if limit else rows,
            row_key="id",
            total=len(rows),
            as_of=iso(end),
            **{
                **self._prov(end, "rain_daily", "rain_anomaly_mm", "spi_12", baseline=True),
                "aggregation": "district-area-weighted",
            },
        )

    async def tbl_extremes(self, limit: int | None) -> c.TableSet:
        kind = self.ctx.select(EXTREMES_KIND)
        w = self.ctx.weights()
        start, end = await self.window()
        totals = (await self.window_totals(start, end)).dropna()
        totals = totals[totals.index.isin(w)].sort_values(ascending=kind == "driest")
        rows = []
        for i, (code, v) in enumerate(totals.items(), 1):
            d = districts.by_code(str(code))
            assert d is not None
            rows.append(
                {
                    "rank": i,
                    "id": d.code,
                    "location": d.official_name,
                    "province": d.province,
                    "total": rnd(float(v), 1),
                }
            )
        n = limit or 10
        return c.TableSet(
            id="extremes",
            title=f"{kind.capitalize()} districts, {start:%d %b %Y} – {end:%d %b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank", align="center"),
                c.Column(key="location", label="Location", type="text"),
                c.Column(key="province", label="Province", type="text"),
                c.Column(key="total", label="Total (12 m)", type="number", unit="mm", decimals=0, align="right"),
            ],
            rows=rows[:n],
            row_key="id",
            link_to="/rainfall?entityId=",
            total=len(rows),
            as_of=iso(end),
            **{**self._prov(end, "rain_daily"), "aggregation": "district"},
        )

    async def tbl_statistics(self, limit: int | None) -> c.TableSet:
        w = self.ctx.weights()
        _, y1 = await self._annual_window()
        y0 = HISTORY_START.year
        ms = await self.monthly_stats()
        rainy = (
            ms.assign(year=ms["date"].dt.to_period("Y").dt.start_time)
            .groupby(["district", "year"], as_index=False)[f"ge_{DRY_DAY_MM:g}"]
            .sum()
        )
        rainy = rainy.rename(columns={"year": "date", f"ge_{DRY_DAY_MM:g}": "value"})
        rainy = rainy[(rainy["date"].dt.year >= y0) & (rainy["date"].dt.year <= y1)]
        ragg = weighted(rainy, w)
        series: dict[str, tuple[str, str, int, pd.Series]] = {
            "Annual rainfall": ("mm", "rain_annual", 0, await self._annual_agg("rain_annual", y0, y1)),
            "Rainy days (≥ 1 mm)": ("days", "rain_daily", 0, ragg.set_index(ragg["date"].dt.year)["value"]),
            "Heavy rain days (R20mm)": ("days", "r20mm", 1, await self._annual_agg("r20mm", y0, y1)),
            "Max 1-day rainfall (Rx1day)": ("mm", "rx1day", 1, await self._annual_agg("rx1day", y0, y1)),
            "Consecutive dry days (CDD)": ("days", "cdd", 0, await self._annual_agg("cdd", y0, y1)),
        }
        rows = []
        for name, (unit, var, nd, s) in series.items():
            s = s.sort_index()
            val, clim = s.get(y1), s[(s.index >= y0) & (s.index <= y1)].mean()
            tr = sens_slope([s.get(y) for y in range(y0, y1 + 1)])
            rows.append(
                {
                    "id": var,
                    "variable": name,
                    "unit": unit,
                    "decimals": nd,  # per row (units differ by row); the columns cover the most precise row (B2)
                    "value": rnd(val, nd),
                    "climatology": rnd(clim, nd),
                    "anomaly": rnd(val - clim, nd) if val is not None else None,
                    "trend": rnd(tr.slope * 10, 2) if tr else None,
                    "trendClass": (tr.trend if tr else None),
                }
            )
        return c.TableSet(
            id="statistics",
            title=f"Rainfall statistics, {y1} vs {y0}–{y1}",
            columns=[
                c.Column(key="variable", label="Variable", type="text"),
                c.Column(key="value", label=f"Value ({y1})", type="number", decimals=1, align="right"),
                c.Column(key="climatology", label=f"Climatology ({y0}–{y1})", type="number", decimals=1, align="right"),
                c.Column(
                    key="anomaly", label="Anomaly", type="delta", decimals=1, good_direction="neutral", align="right"
                ),
                c.Column(
                    key="trend", label="Trend (Sen's slope)", type="number", unit="/decade", decimals=2, align="right"
                ),
            ],
            rows=rows,
            row_key="id",
            as_of=f"{y1}-01-01",
            **{
                **self._prov(dt.date(y1, 12, 31), "rain_annual", "rain_daily", "r20mm", "rx1day", "cdd", res="annual"),
                "baseline": f"{y0}–{y1} (full years)",
                "baselineNote": self.ctx.state.registry.baseline.note,
            },
        )

    async def tbl_sources(self, limit: int | None) -> c.TableSet:
        reg = self.ctx.state.registry
        stats = {s.table: s for s in await self.ctx.state.meta.table_stats()}
        rows = []
        for var, label in (
            ("rain_daily", "District daily rainfall"),
            ("rain_daily_chirps", "CHIRPS daily rainfall estimate"),
            ("rain_dekad_tamsat", "TAMSAT rainfall estimate (mm per dekad, one value per month)"),
            ("spi_6", "SPI (1–24 months)"),
            ("spei_6", "SPEI (1–24 months)"),
            ("r20mm", "ETCCDI rainfall indices (annual)"),
        ):
            src = reg.canonical_source(var)
            if src is None:
                continue
            ds, col = src
            st = stats.get(ds.table)
            # the column's own resolution (values repeated on daily rows count once per period), not the table's:
            # TAMSAT is stored on daily rows but holds one value per month (B14; spec §6.1 expects dekadal)
            cat = next(x for x in get_catalogue().categories if x.table == ds.table).column(col)
            res = cat.resolution if cat is not None else ds.time.resolution
            rows.append(
                {
                    "id": f"{ds.table}.{col}",
                    "source": label,
                    "table": ds.table,
                    "stations": None,
                    "resolution": f"52 districts, {res}",
                    "cadence": res,
                    "asOf": iso(st.last_date) if st else None,
                }
            )
        return c.TableSet(
            id="sources",
            title="Rainfall data sources",
            columns=[
                c.Column(key="source", label="Source", type="text"),
                c.Column(key="stations", label="Stations", type="number", decimals=0, align="right"),
                c.Column(key="resolution", label="Resolution", type="text"),
                c.Column(key="cadence", label="Cadence", type="text"),
            ],
            rows=rows,
            row_key="id",
            as_of=max((r["asOf"] for r in rows if r["asOf"]), default=None),
            unavailable=[c.UnavailableField(field="rows.stations", reason="No station data in the database")],
            source=sorted({r["table"] for r in rows}),
        )

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer in ("forecast-1", "forecast-2", "forecast-3"):  # district rainfall forecasts (app.forecasting.pages)
            lead = int(layer[-1])
            plural = "s" if lead > 1 else ""
            return fc.outlook_map(
                self.ctx,
                "rainfall",
                lead,
                layer=layer,
                title=f"Rainfall outlook +{lead} month{plural} (anomaly, % of the same-month mean)",
                scale="anomalyPct",
                unit="%",
                decimals=1,
                kind="pct-normal",
            )
        w = self.ctx.weights()
        start, end = await self.window()
        newest = await self.newest_daily()
        month = await self.latest_complete_month()
        y1 = (await self._annual_window())[1]
        spec: dict[str, tuple[str, str, str]] = {
            "total": ("Total rainfall, last 12 months (mm)", "rainfall", "mm"),
            "municipal-total": ("Rainfall last 12 months by district (mm)", "rainfall", "mm"),
            "anomaly": ("Rainfall anomaly, 12 months (% of normal)", "anomalyPct", "%"),
            "monthly": (f"Monthly rainfall – {newest:%b %Y} (mm)", "rainfallMonthly", "mm"),
            "map": (f"Mean annual precipitation {HISTORY_START.year}–{y1} (mm)", "rainfall", "mm"),
            "spi1": ("SPI-1", "spi", "—"),
            "spi3": ("SPI-3", "spi", "—"),
            "spi6": ("SPI-6", "spi", "—"),
            "spi12": ("SPI-12", "spi", "—"),
            "spei6": ("SPEI-6", "spi", "—"),
            "cdd": (f"Consecutive dry days (current, to {newest:%d %b})", "days", "days"),
            "r20": (f"R20mm days ({y1})", "days", "days"),
        }
        if layer not in spec:
            raise unknown("layer", layer, [*spec, "forecast-1", "forecast-2", "forecast-3"])
        title, scale, unit = spec[layer]
        baseline = False
        if layer in ("total", "municipal-total", "anomaly"):
            totals = await self.window_totals(start, end)
            if layer == "anomaly":
                n = window_normal(await self.normals(), start, end)
                vals, nd = (totals - n) / n * 100, 1
                baseline = True
            else:
                vals, nd = totals, 1
            date, variables = end, ["rain_daily", "rain_anomaly_mm"] if baseline else ["rain_daily"]
        elif layer == "monthly":
            ms = await self.monthly_stats()
            vals = ms[ms["date"] == pd.Timestamp(newest.replace(day=1))].set_index("district")["sum"]
            nd, date, variables = 1, newest, ["rain_daily"]
            if not is_complete_month(newest.replace(day=1), newest):
                title += f" to {newest:%d %b}"
        elif layer == "map":
            df = await self.data.annual("rain_annual")
            df = df[(df["date"].dt.year >= HISTORY_START.year) & (df["date"].dt.year <= y1)]
            vals, nd, date, variables = df.groupby("district")["value"].mean(), 0, dt.date(y1, 12, 31), ["rain_annual"]
        elif layer.startswith(("spi", "spei")):
            var = f"spei_{layer[4:]}" if layer.startswith("spei") else f"spi_{layer[3:]}"
            df = await self.data.monthly(var)
            vals = df[df["date"] == pd.Timestamp(month)].set_index("district")["value"]
            nd, date, variables = 2, month, [var]
            title += f" – {month:%b %Y}"
        elif layer == "cdd":
            vals, nd, date, variables = await self.current_dry_spell(newest), 0, newest, ["rain_daily"]
        else:  # r20
            df = await self.data.annual("r20mm")
            vals = df[df["date"].dt.year == y1].set_index("district")["value"]
            nd, date, variables = 0, dt.date(y1, 12, 31), ["r20mm"]
        values = {code: rnd(float(v), nd) for code, v in vals.items() if code in w}
        annual, monthly = ("rain_annual", "r20mm"), ("spi_", "spei_")
        map_res: Resolution = (
            "annual" if variables[0] in annual else "monthly" if variables[0].startswith(monthly) else "daily"
        )
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
            values=values,
            geometry="districts",
            **prov,
        ).dump()

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        sel = self.ctx.filters.scope
        items = [
            c.Entity(
                id=d.code,
                name=d.official_name,
                type="district",
                province=d.province,
                sub=f"{districts.PROVINCES[d.province]} · {d.kind}",
            )
            for d in sorted(districts.districts(), key=lambda d: d.official_name)
            if d.code in sel.district_codes or sel.level == "national"
        ]
        return c.EntityList(type="district", total=len(items), items=items).dump()

    async def entity(self, code: str) -> dict[str, Any]:
        d = districts.by_code(code)
        if d is None:
            raise NotFound(f"Unknown entity {code!r}")
        newest = await self.newest_daily()
        start = newest - pd.DateOffset(years=1) + pd.Timedelta(days=1)
        start_d = _d(start)
        tot = (await self.window_totals(start_d, newest)).get(d.code)
        n = window_normal(await self.normals(), start_d, newest).get(d.code)
        month = await self.latest_complete_month()
        fields: list[c.EntityField] = [
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            c.EntityField(label="Rainfall, last 12 months", value=rnd(tot, 0), unit="mm", as_of=iso(newest)),
            c.EntityField(
                label="Anomaly, last 12 months",
                value=rnd((tot - n) / n * 100, 0) if tot and n else None,
                unit="%",
                scale="anomalyPct",
                as_of=iso(newest),
            ),
        ]
        spi6 = None
        for var, label in (("spi_3", "SPI-3"), ("spi_6", "SPI-6"), ("spi_12", "SPI-12")):
            df = await self.data.monthly(var)
            v = df[(df["district"] == d.code) & (df["date"] == pd.Timestamp(month))]["value"]
            val = float(v.iloc[0]) if not v.empty else None
            spi6 = val if var == "spi_6" else spi6
            fields.append(
                c.EntityField(label=f"{label} ({month:%b %Y})", value=rnd(val, 2), scale="spi", as_of=iso(month))
            )
        areas = self.ctx.state.spatial.district_areas()
        if areas:
            fields.append(c.EntityField(label="Area", value=round(areas[d.code]), unit="km²"))
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type="district",
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code}",
            title=d.official_name,
            **drought_status(spi6),
            fields=fields,
            image="municipality",
            as_of=iso(newest),
            source=self.src("rain_daily", "spi_6"),
        ).dump()


async def prewarm(data: DataAccess) -> None:
    """Load the frames every rainfall request needs (called in the background at startup)."""
    for var in ("rain_monthly", "rain_anomaly_mm", "spi_6", "spei_6"):
        await data.monthly(var)
    await data.daily_monthly_stats("rain_daily", (DRY_DAY_MM, 20.0, EVENT_MM))
    newest = await data.newest("rain_daily")
    start, end = parse_window(None, None, None).resolve(newest, HISTORY_START)  # default 12M window
    await data.daily_window_stats("rain_daily", start, end, (20.0,))
    await data.daily_window_stats("rain_daily", _shift_year(start), _shift_year(end), (20.0,))
    await data.daily_window_stats(
        "rain_daily", newest - dt.timedelta(days=CDD_LOOKBACK_DAYS - 1), newest, (DRY_DAY_MM,)
    )


def _shift_year(d: dt.date) -> dt.date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - 1, day=28)
