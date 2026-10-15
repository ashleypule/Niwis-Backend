"""Vegetation Condition (contract module `vegetation`, spec §5; DB Module 3).

Source (decision D4): `satellite_remote_sensing` — the vegetation source that passed the plausibility check, shared
with the drought page so a district-month never shows two values. It is monthly and ends 2025-12, so values are
`isStale`. `5.1_…_sansa_master` failed and is withheld entirely: EVI, SAVI, MSAVI2, VPI, ASI, SVI and NDII (which
only it provides) are unavailable. Drivers: rainfall (`6.1`), temperature (`4_1`), ET₀ (`meteorological`), GDD (`5.2`),
LST (`satellite`).

Methods (docs/DATA_MAPPING.md §4): area-weighted district means (≥ 95 % coverage); NDVI normal = same calendar month
2001–2025 (the satellite record's full years); stress = VHI < 40 % (thresholds.json `vegetation.stress_below`);
health classes from VCI / VHI via `veg_class`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from app.core import districts
from app.core.aggregate import class_areas, coverage_reason, short_of_coverage, weighted, weighted_scalar
from app.core.data import DataAccess
from app.core.errors import BadRequest, DataUnavailable
from app.core.palettes import NEGATIVE, OBSERVED, POSITIVE, SERIES
from app.core.provenance import Resolution, iso
from app.core.stats import rnd, sens_slope
from app.core.thresholds import thresholds, veg_class
from app.modules.common import Ctx, Selector, anomaly_split_reasons, forecast_unavailable, unknown
from app.modules.drivers import driver_series
from app.modules.rainfall.service import RainfallService, _months
from app.schemas import contract as c

MODULE = "vegetation"
CLIM = (2001, 2025)
VEG_CLASSES = ["Very Healthy", "Healthy", "Moderate", "Stressed", "Very Stressed"]
VEG_COLOURS = {
    "Very Healthy": "#1A9850",
    "Healthy": "#A6D96A",
    "Moderate": "#FEE08B",
    "Stressed": "#F46D43",
    "Very Stressed": "#A50026",
}  # `vci` palette, reference/DATA_DICTIONARY.md
ONLY_IN_5_1 = {
    "evi": "EVI",
    "savi": "SAVI",
    "msavi2": "MSAVI2",
    "vpi": "VPI",
    "asi": "ASI",
    "svi": "SVI",
    "ndii": "NDII",
}
SEASONS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
# Indices served from satellite_remote_sensing (D4): id → (variable, label, unit, decimals)
LIVE_INDICES: dict[str, tuple[str, str, str, int]] = {
    "ndvi": ("ndvi", "NDVI", "—", 3),
    "vci": ("vci", "VCI", "%", 1),
    "vhi": ("vhi", "VHI", "%", 1),
    "tci": ("tci", "TCI", "%", 1),
    "ndwi": ("ndwi", "NDWI", "—", 3),
    "lst": ("lst", "LST", "°C", 1),
    "sm": ("soil_moisture_surface", "Soil moisture", "m³/m³", 3),
}
WITHHELD_UNITS = {"evi": "—", "savi": "—", "msavi2": "—", "vpi": "%", "asi": "%", "svi": "—", "ndii": "—"}
INDEX_IDS = (*LIVE_INDICES, *ONLY_IN_5_1)
INDEX_SET = Selector("set", INDEX_IDS, "ndvi,evi,savi,msavi2", multi=True)
INDEX = Selector("index", INDEX_IDS, "ndvi")
DRIVER_VAR = Selector("var", ("rain", "temp", "pet", "sm"), "rain")
SELECTORS: dict[tuple[str, str], tuple[Selector, ...]] = {
    ("timeseries", "indices"): (INDEX_SET,),
    ("timeseries", "index-trend"): (INDEX,),
    ("timeseries", "drivers"): (DRIVER_VAR,),
}


def _d(ts: Any) -> dt.date:
    d: dt.date = pd.Timestamp(ts).date()
    return d


def withheld(var: str) -> DataUnavailable:
    return DataUnavailable(
        f"{ONLY_IN_5_1.get(var, var.upper())} is only provided by "
        "5.1_primary_satellite_vegetation_variables_sansa_master, which failed the NDVI plausibility check and is "
        "withheld (docs/DATA_GAPS.md §5)",
        missing=[var],
    )


class VegetationService:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.data: DataAccess = ctx.data
        self.rain = RainfallService(ctx)

    # ---------------------------------------------------------------- helpers

    def src(self, *variables: str) -> list[str]:
        return self.rain.src(*variables)

    def _prov(
        self, as_of: dt.date | None, *variables: str, baseline: str | None = None, res: Resolution = "daily"
    ) -> dict[str, Any]:
        p = self.ctx.provenance(as_of, self.src(*variables), res=res)
        p.pop("asOf")
        if baseline:
            p["baseline"] = baseline
            p["baselineNote"] = self.ctx.state.registry.baseline.note
        return p

    def kpi_extra(self, as_of: dt.date | None, *variables: str, res: Resolution = "daily") -> dict[str, Any]:
        return {"as_of": iso(as_of), "source": self.src(*variables), **self.ctx.stamp(as_of, res)}

    async def last(self, var: str = "ndvi") -> dt.date:
        return _d((await self.data.monthly(var))["date"].max())

    async def at(self, var: str, month: dt.date) -> pd.Series:
        df = await self.data.monthly(var)
        return df[df["date"] == pd.Timestamp(month)].set_index("district")["value"]

    async def normal_at(self, var: str, month: int) -> pd.Series:
        """Per district mean of `var` for a calendar month over 2001–2025."""
        df = await self.data.monthly(var)
        sub = df[df["date"].dt.year.between(*CLIM) & (df["date"].dt.month == month)]
        return sub.groupby("district")["value"].mean()

    async def window_months(self, last: dt.date) -> list[pd.Timestamp]:
        start, _ = await self.rain.window()
        first = min(pd.Timestamp(start.replace(day=1)), pd.Timestamp(last))
        return _months(first.date(), last)

    async def scope_series(self, var: str) -> pd.Series:
        return weighted(await self.data.monthly(var), self.ctx.weights()).set_index("date")["value"]

    async def stressed_share(self, month: dt.date, w: dict[str, float]) -> float | None:
        vhi = (await self.at("vhi", month)).dropna()
        total = sum(w.values())
        have = sum(w[k] for k in vhi.index if k in w)
        if total <= 0 or have < 0.95 * total:
            return None
        below = thresholds()["vegetation"]["stress_below"]
        return sum(w[k] for k, v in vhi.items() if k in w and v < below) / total * 100

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        w = self.ctx.weights()
        m = await self.last()
        kpis: list[c.Kpi] = []
        specs = [
            ("ndvi", "ndvi", "NDVI (Mean)", None, 2),
            ("vci", "vci", "VCI (Current)", "%", 0),
            ("vhi", "vhi", "VHI (Current)", "%", 0),
            ("tci", "tci", "TCI (Current)", "%", 0),
            ("lst", "lst", "Land Surface Temp (LST)", "°C", 1),
            ("soilMoisture", "soil_moisture_surface", "Soil Moisture (Surface)", "m³/m³", 2),
        ]
        for kid, var, label, unit, dec in specs:
            v = weighted_scalar(await self.at(var, m), w)
            k = c.Kpi(
                id=kid,
                metric=self.ctx.state.registry.variables[var].metric or var,
                label=label,
                sublabel=f"{m:%b %Y}",
                value=rnd(v, 3 if var in ("ndvi", "soil_moisture_surface") else 1),
                decimals=dec,
                **self.kpi_extra(m, var, res="monthly"),
            )
            if unit:
                k.unit = unit
            if var in ("vci", "vhi", "tci") and v is not None:
                k.status = c.Status(scale="vci", id=veg_class(v), label=veg_class(v))
            kpis.append(k)
        stress = await self.stressed_share(m, w)
        kpis.append(
            c.Kpi(
                id="stress",
                metric="vegStressArea",
                label="Area Under Stress",
                sublabel="VHI < 40 %",
                value=rnd(stress, 1),
                unit="%",
                decimals=0,
                **self.kpi_extra(m, "vhi", res="monthly"),
            )
        )
        cur, nor = (
            weighted_scalar(await self.at("ndvi", m), w),
            weighted_scalar(await self.normal_at("ndvi", m.month), w),
        )
        kpis.append(
            c.Kpi(
                id="anomaly",
                metric="vegAnomaly",
                label="Vegetation Anomaly",
                sublabel=f"NDVI vs {m:%b} {CLIM[0]}–{CLIM[1]} mean",
                value=rnd((cur - nor) / nor * 100, 1) if cur is not None and nor else None,
                unit="%",
                decimals=0,
                **self.kpi_extra(m, "ndvi", res="monthly"),
            )
        )
        body = c.KpiSet(
            module=MODULE,
            as_of=iso(m) or "",
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
            ],
            **self._prov(
                m,
                "ndvi",
                "vci",
                "vhi",
                "tci",
                "lst",
                "soil_moisture_surface",
                baseline=f"{CLIM[0]}–{CLIM[1]} same-month mean",
                res="monthly",
            ),
        )
        return body.dump()

    # ---------------------------------------------------------------- time series

    async def timeseries(self, metric: str) -> dict[str, Any]:
        handlers = {
            "indices": self.ts_indices,
            "index-trend": self.ts_index_trend,
            "anomaly": self.ts_anomaly,
            "drivers": self.ts_drivers,
        }
        if metric not in handlers:
            raise unknown("metric", metric, list(handlers))
        return (await handlers[metric]()).dump()

    async def ts_indices(self) -> c.SeriesSet:
        """The indices in `set` (default NDVI, EVI, SAVI, MSAVI2). Indices only the withheld 5.1 table provides are
        null series with an `unavailable` entry; at most two units per request (one per axis)."""
        ids = self.ctx.select_many(INDEX_SET)
        units = list(dict.fromkeys(LIVE_INDICES[i][2] if i in LIVE_INDICES else WITHHELD_UNITS[i] for i in ids))
        if len(units) > 2:
            raise BadRequest(
                f"`set` mixes {len(units)} units ({', '.join(units)}); a chart has at most two axes",
                issues=[{"param": "set", "value": ",".join(ids), "units": units}],
            )
        m = await self.last()
        ms = await self.window_months(m)
        reason = "Only in the withheld 5.1 source (failed the NDVI plausibility check, docs/DATA_GAPS.md §5)"
        series, unavailable, variables = [], [], []
        for i, idx in enumerate(ids):
            unit = LIVE_INDICES[idx][2] if idx in LIVE_INDICES else WITHHELD_UNITS[idx]
            axis = units.index(unit)
            if idx in LIVE_INDICES:
                var, label, _, nd = LIVE_INDICES[idx]
                s = await self.scope_series(var)
                data = [[iso(x.date()), rnd(s.get(x), nd)] for x in ms]
                variables.append(var)
            else:
                label, data = ONLY_IN_5_1[idx], [[iso(x.date()), None] for x in ms]
                unavailable.append(c.UnavailableField(field=f"series.{idx}", reason=reason))
            series.append(
                c.Series(key=idx, name=label, kind="line", axis=axis, color=SERIES[i % len(SERIES)], data=data)
            )
        extra: dict[str, Any] = {"unavailable": unavailable} if unavailable else {}
        return c.SeriesSet(
            id="indices",
            title="Vegetation indices",
            x_type="time",
            axes=[c.Axis(unit=u, label="Index value" if u in ("—", "%") else u) for u in units],
            series=series,
            as_of=iso(m),
            **extra,
            **self._prov(m, *(variables or ["ndvi"]), res="monthly"),
        )

    async def ts_index_trend(self) -> c.SeriesSet:
        idx = self.ctx.select(INDEX)
        if idx in ONLY_IN_5_1:
            raise withheld(idx)
        var, label, unit, nd = LIVE_INDICES[idx]
        m = await self.last(var)
        ms = _months(_d(pd.Timestamp(m) - pd.DateOffset(months=11)), m)
        ndvi = await self.scope_series(var)
        clim = ndvi[(ndvi.index.year >= CLIM[0]) & (ndvi.index.year <= CLIM[1])]
        by = clim.groupby(clim.index.month).agg(["mean", "std"])
        return c.SeriesSet(
            id="index-trend",
            title=f"{label} vs normal",
            x_type="time",
            axes=[c.Axis(unit=unit, label=label)],
            series=[
                c.Series(
                    key="band",
                    name=f"Normal range (±1 s.d., {CLIM[0]}–{CLIM[1]})",
                    kind="band",
                    color="#C7E9A0",
                    data=[
                        [
                            iso(x.date()),
                            rnd(by["mean"].get(x.month) - by["std"].get(x.month), nd),
                            rnd(by["mean"].get(x.month) + by["std"].get(x.month), nd),
                        ]
                        for x in ms
                    ],
                ),
                c.Series(
                    key="current",
                    name=f"{label} (current)",
                    kind="line",
                    color=OBSERVED,
                    symbol=True,
                    data=[[iso(x.date()), rnd(ndvi.get(x), nd)] for x in ms],
                ),
                c.Series(
                    key="previous",
                    name=f"{label} (previous year)",
                    kind="line",
                    color=SERIES[0],
                    dash=True,
                    data=[[iso(x.date()), rnd(ndvi.get(x - pd.DateOffset(years=1)), nd)] for x in ms],
                ),
            ],
            as_of=iso(m),
            note="Contract label '1991–2020' → 2001–2025 (satellite record). Previous year is plotted on "
            "the current year's months.",
            **self._prov(m, var, baseline=f"{CLIM[0]}–{CLIM[1]} same-month mean ± 1 s.d.", res="monthly"),
        )

    async def ts_anomaly(self) -> c.SeriesSet:
        m = await self.last()
        ms = await self.window_months(m)
        ndvi = await self.scope_series("ndvi")
        clim = ndvi[(ndvi.index.year >= CLIM[0]) & (ndvi.index.year <= CLIM[1])]
        normal = clim.groupby(clim.index.month).mean()
        an = {
            x: (ndvi.get(x) - normal.get(x.month)) / normal.get(x.month) * 100
            if ndvi.get(x) is not None and normal.get(x.month)
            else None
            for x in ms
        }
        an = {x: None if a is None or pd.isna(a) else a for x, a in an.items()}
        anomaly_split_reasons(self.ctx, list(an.values()), "NDVI anomaly")
        return c.SeriesSet(
            id="anomaly",
            title="NDVI anomaly",
            x_type="time",
            axes=[c.Axis(unit="%", label="NDVI anomaly")],
            series=[
                c.Series(
                    key="pos",
                    name="Positive anomaly",
                    kind="bar",
                    stack="a",
                    color=POSITIVE,
                    data=[[iso(x.date()), rnd(a, 1) if a is not None and a >= 0 else None] for x, a in an.items()],
                ),
                c.Series(
                    key="neg",
                    name="Negative anomaly",
                    kind="bar",
                    stack="a",
                    color=NEGATIVE,
                    data=[[iso(x.date()), rnd(a, 1) if a is not None and a < 0 else None] for x, a in an.items()],
                ),
            ],
            as_of=iso(m),
            **self._prov(m, "ndvi", baseline=f"{CLIM[0]}–{CLIM[1]} same-month mean", res="monthly"),
        )

    async def ts_drivers(self) -> c.SeriesSet:
        return await driver_series(self.ctx, self.rain, self.ctx.select(DRIVER_VAR))

    # ---------------------------------------------------------------- forecast

    async def forecast(self, metric: str) -> dict[str, Any]:
        if metric not in ("ndvi", "recovery-probability"):
            raise unknown("metric", metric, ["ndvi", "recovery-probability"])
        raise forecast_unavailable(MODULE, metric)

    # ---------------------------------------------------------------- breakdown

    async def breakdown(self, metric: str) -> dict[str, Any]:
        m = await self.last()
        if metric == "land-cover":
            raise DataUnavailable("No land-cover data per district in the database", missing=["land_cover"])
        if metric == "classification":
            w = self.ctx.weights()
            a = class_areas((await self.at("vhi", m)).dropna().map(veg_class), w)
            total = sum(w.values())
            return c.CategorySet(
                id="classification",
                title=f"Vegetation health (VHI), {m:%b %Y}",
                unit="%",
                categories=VEG_CLASSES,
                colors=[VEG_COLOURS[k] for k in VEG_CLASSES],
                series=[
                    c.CategorySeries(
                        key="area",
                        name="Area",
                        values=[
                            rnd(a.get(k, 0.0) / total * 100, 1) if sum(a.values()) >= 0.95 * total else None
                            for k in VEG_CLASSES
                        ],
                    )
                ],
                as_of=iso(m),
                **self._prov(m, "vhi", res="monthly"),
            ).dump()
        if metric == "by-province":
            areas = self.ctx.state.spatial.district_areas()
            if areas is None:
                raise DataUnavailable("Province areas need district boundaries", missing=["district boundaries"])
            cls = (await self.at("vhi", m)).dropna().map(veg_class)
            groups = {
                districts.PROVINCES[p]: {d.code: areas[d.code] for d in districts.in_province(p)}
                for p in districts.PROVINCES
            }
            if short := short_of_coverage(set(cls.index), groups):
                reason = coverage_reason(f"VHI for {m:%b %Y}", short, len(groups))
                for k in VEG_CLASSES:
                    self.ctx.explain(f"series.{k.lower().replace(' ', '-')}", reason)
            shares = []
            for p in districts.PROVINCES:
                w = {d.code: areas[d.code] for d in districts.in_province(p)}
                a = class_areas(cls, w)
                ok = sum(a.values()) >= 0.95 * sum(w.values())
                shares.append({k: (a.get(k, 0.0) / sum(w.values()) * 100 if ok else None) for k in VEG_CLASSES})
            return c.CategorySet(
                id="by-province",
                title=f"Vegetation health by province (VHI), {m:%b %Y}",
                unit="%",
                categories=list(districts.PROVINCES.values()),
                series=[
                    c.CategorySeries(
                        key=k.lower().replace(" ", "-"),
                        name=k,
                        stack="health",
                        color=VEG_COLOURS[k],
                        values=[rnd(s[k], 1) for s in shares],
                    )
                    for k in VEG_CLASSES
                ],
                as_of=iso(m),
                **{**self._prov(m, "vhi", res="monthly"), "aggregation": "district-area-weighted"},
            ).dump()
        if metric == "seasonal":
            ndvi = await self.scope_series("ndvi")
            last_full = m.year if m.month == 12 else m.year - 1
            years = list(range(last_full - 6, last_full + 1))
            series = []
            for i, (name, months) in enumerate(SEASONS.items()):
                vals = []
                for y in years:
                    # DJF of year y = Dec (y-1) + Jan, Feb (y)
                    dates = [pd.Timestamp(y - 1 if mm == 12 else y, mm, 1) for mm in months]
                    v = [ndvi.get(d) for d in dates]
                    vals.append(rnd(sum(v) / 3, 3) if all(x is not None for x in v) else None)
                series.append(c.CategorySeries(key=name.lower(), name=name, color=SERIES[i], values=vals))
            return c.CategorySet(
                id="seasonal",
                title="Seasonal mean NDVI",
                unit="—",
                categories=[str(y) for y in years],
                series=series,
                as_of=iso(m),
                **self._prov(m, "ndvi", res="monthly"),
            ).dump()
        if metric == "drivers":
            return (await self.drivers()).dump()
        raise unknown("metric", metric, ["land-cover", "classification", "by-province", "seasonal", "drivers"])

    async def drivers(self) -> c.CategorySet:
        """% change vs the same 6 months' 2001–2025 mean, each driver over its own latest 6 complete months."""
        w = self.ctx.weights()
        complete = await self.rain.latest_complete_month()
        specs: list[tuple[str, str, str, pd.DataFrame]] = []
        rain_dm = (await self.rain.monthly_stats()).rename(columns={"sum": "value"})
        specs.append(("Rainfall", "rain_daily", "sum", rain_dm[["district", "date", "value"]]))
        tdm = await self.data.daily_monthly_stats("tavg", ())
        specs.append(("Mean temperature", "tavg", "mean", tdm.assign(value=tdm["sum"] / tdm["days"])))
        edm = await self.data.daily_monthly_stats("et0_daily", ())
        specs.append(("Reference ET₀", "et0_daily", "sum", edm.rename(columns={"sum": "value"})))
        specs.append(("Growing degree days", "gdd", "sum", await self.data.monthly("gdd")))
        specs.append(("Land surface temperature", "lst", "mean", await self.data.monthly("lst")))
        cats: list[str] = []
        vals: list[float | None] = []
        comps: list[c.Component] = []
        for name, var, how, df in specs:
            agg = weighted(df[["district", "date", "value"]], w).set_index("date")["value"].dropna()
            cats.append(name)
            if agg.empty:  # no month with ≥ 95 % area coverage in this scope
                vals.append(None)
                continue
            end = min(agg.index.max(), pd.Timestamp(complete)) if var != "lst" else agg.index.max()
            months = [end - pd.DateOffset(months=i) for i in range(6)]
            cur = [agg.get(x) for x in months]
            normal = [
                agg[(agg.index.month == x.month) & (agg.index.year >= CLIM[0]) & (agg.index.year <= CLIM[1])].mean()
                for x in months
            ]
            if any(v is None for v in cur):
                vals.append(None)
            else:
                f = sum if how == "sum" else (lambda xs: sum(xs) / len(xs))
                vals.append(rnd((f(cur) - f(normal)) / f(normal) * 100, 1))
            comps.append(
                c.Component(
                    key=var, as_of=iso(end.date()), source=self.src(var), **self.ctx.stamp(end.date(), "monthly")
                )
            )
        as_of = max((dt.date.fromisoformat(x.as_of) for x in comps if x.as_of), default=None)
        return c.CategorySet(
            id="drivers",
            title="Drivers: change vs normal (last 6 months)",
            unit="%",
            categories=cats,
            series=[c.CategorySeries(key="change", name="Change vs normal (6 months)", values=vals)],
            as_of=iso(as_of),
            components=comps,
            **{
                **self._prov(
                    as_of,
                    "rain_daily",
                    "tavg",
                    "et0_daily",
                    "gdd",
                    "lst",
                    baseline=f"{CLIM[0]}–{CLIM[1]} same-months mean",
                    res="monthly",
                ),
                "isStale": any(x.is_stale for x in comps),
            },
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric == "forecast-indicators":
            raise forecast_unavailable(MODULE, metric)
        if metric not in ("provinces", "hotspots"):
            raise unknown("metric", metric, ["provinces", "hotspots", "forecast-indicators"])
        m = await self.last()
        ndvi, vci = await self.at("ndvi", m), await self.at("vci", m)
        norm = await self.normal_at("ndvi", m.month)
        if metric == "provinces":
            areas = self.ctx.state.spatial.district_areas()
            if areas is None:
                raise DataUnavailable("Province aggregates need district boundaries", missing=["district boundaries"])
            groups = {
                districts.PROVINCES[p]: {d.code: areas[d.code] for d in districts.in_province(p)}
                for p in districts.PROVINCES
            }
            for cols, ser, what in (
                (("ndvi", "change"), ndvi, f"NDVI for {m:%b %Y}"),
                (("vci", "status"), vci, f"VCI for {m:%b %Y}"),
            ):
                if short := short_of_coverage(set(ser.dropna().index), groups):
                    for col in cols:
                        self.ctx.explain(f"rows.{col}", coverage_reason(what, short, len(groups)))
            rows = []
            for p in districts.PROVINCES:
                w = {d.code: areas[d.code] for d in districts.in_province(p)}
                n, v, nn = weighted_scalar(ndvi, w), weighted_scalar(vci, w), weighted_scalar(norm, w)
                rows.append(
                    {
                        "id": p,
                        "province": districts.PROVINCES[p],
                        "ndvi": rnd(n, 3),
                        "vci": rnd(v, 1),
                        "status": veg_class(v) if v is not None else None,
                        "change": rnd((n - nn) / nn * 100, 1) if n is not None and nn else None,
                    }
                )
            rows.sort(key=lambda r: -float(r["vci"]) if r["vci"] is not None else float("inf"))
            for i, r in enumerate(rows, 1):
                r["rank"] = i
            return c.TableSet(
                id="provinces",
                title=f"Vegetation condition by province, {m:%b %Y}",
                columns=[
                    c.Column(key="rank", label="#", type="rank", align="center"),
                    c.Column(key="province", label="Province", type="text"),
                    c.Column(key="ndvi", label="Mean NDVI", type="number", decimals=2, scale="ndvi", align="right"),
                    c.Column(key="vci", label="VCI", type="number", unit="%", decimals=0, scale="vci", align="right"),
                    c.Column(key="status", label="Health status", type="text"),
                    c.Column(
                        key="change",
                        label="Change",
                        type="delta",
                        unit="%",
                        decimals=0,
                        good_direction="up",
                        align="right",
                    ),
                ],
                rows=rows,
                row_key="id",
                total=len(rows),
                as_of=iso(m),
                **{
                    **self._prov(m, "ndvi", "vci", baseline=f"{CLIM[0]}–{CLIM[1]} same-month mean", res="monthly"),
                    "aggregation": "district-area-weighted",
                },
            ).dump()
        w = self.ctx.weights()
        df = pd.DataFrame({"ndvi": ndvi, "vci": vci, "norm": norm})
        df = df[df.index.isin(w)].dropna(subset=["vci"]).sort_values("vci")
        rows = []
        for i, (code, r) in enumerate(df.iterrows(), 1):
            d = districts.by_code(str(code))
            assert d is not None
            rows.append(
                {
                    "rank": i,
                    "id": d.code,
                    "municipality": d.official_name,
                    "province": d.province,
                    "change": rnd(r["ndvi"] - r["norm"], 3),
                    "vci": rnd(r["vci"], 1),
                    "stress": veg_class(r["vci"]),
                }
            )
        return c.TableSet(
            id="hotspots",
            title=f"Vegetation stress hotspots (lowest VCI), {m:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank", align="center"),
                c.Column(key="municipality", label="Municipality", type="text"),
                c.Column(key="province", label="Prov.", type="text"),
                c.Column(
                    key="change", label="NDVI change", type="delta", decimals=2, good_direction="up", align="right"
                ),
                c.Column(key="vci", label="VCI", type="number", unit="%", decimals=0, scale="vci", align="right"),
                c.Column(key="stress", label="Stress", type="text"),
            ],
            rows=rows[: limit or 10],
            row_key="id",
            total=len(rows),
            as_of=iso(m),
            **{
                **self._prov(m, "ndvi", "vci", baseline=f"{CLIM[0]}–{CLIM[1]} same-month mean", res="monthly"),
                "aggregation": "district",
            },
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer in ONLY_IN_5_1:
            raise withheld(layer)
        if layer in ("forecast-ndvi", "forecast-anomaly"):
            raise forecast_unavailable(MODULE, layer)
        w = self.ctx.weights()
        m = await self.last()
        simple = {
            "ndvi": ("ndvi", "NDVI", "ndvi", "—", 3),
            "vci": ("vci", "VCI (%)", "vci", "%", 1),
            "vhi": ("vhi", "VHI (%)", "vci", "%", 1),
            "tci": ("tci", "TCI (%)", "vci", "%", 1),
            "ndwi": ("ndwi", "NDWI", "ndvi", "—", 3),
            "lst": ("lst", "LST (°C)", "lst", "°C", 1),
            "sm": ("soil_moisture_surface", "Soil moisture (m³/m³)", "soilMoisture", "m³/m³", 3),
        }
        baseline = None
        if layer in simple:
            var, label, scale, unit, nd = simple[layer]
            vals = (await self.at(var, m)).map(lambda v: rnd(v, nd))
            title, variables = f"{label} – {m:%b %Y}", [var]
        elif layer == "ndvi-anomaly":
            vals = (await self.at("ndvi", m) - await self.normal_at("ndvi", m.month)).map(lambda v: rnd(v, 3))
            title = f"NDVI anomaly vs same month {CLIM[0]}–{CLIM[1]} – {m:%b %Y}"
            scale, unit, variables, baseline = "ndviAnomaly", "—", ["ndvi"], f"{CLIM[0]}–{CLIM[1]} same-month mean"
        elif layer == "ndvi-trend":
            df = await self.data.monthly("ndvi")
            ann = (
                df[df["date"].dt.year.between(*CLIM)]
                .assign(y=lambda x: x["date"].dt.year)
                .groupby(["district", "y"])["value"]
                .mean()
            )
            years = range(CLIM[0], CLIM[1] + 1)
            out = {}
            for code in ann.index.get_level_values(0).unique():
                s = ann.loc[code]
                tr = sens_slope([s.get(y) for y in years])
                out[code] = rnd(tr.slope * 10, 4) if tr else None
            vals = pd.Series(out)
            title = f"NDVI trend (Sen's slope per decade, {CLIM[0]}–{CLIM[1]})"
            scale, unit, variables = "ndviTrend", "/decade", ["ndvi"]
        else:
            raise unknown(
                "layer",
                layer,
                [*simple, "ndvi-anomaly", "ndvi-trend", *ONLY_IN_5_1, "forecast-ndvi", "forecast-anomaly"],
            )
        prov = self.ctx.provenance(m, self.src(*variables), res="monthly")
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
            date=iso(m) or "",
            values={str(k): v for k, v in vals.items() if k in w},
            geometry="districts",
            **prov,
        ).dump()


async def prewarm(data: DataAccess) -> None:
    for var in ("ndvi", "vci", "vhi", "tci", "lst", "ndwi", "soil_moisture_surface", "gdd"):
        await data.monthly(var)
