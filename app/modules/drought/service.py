"""Drought Status Monitoring (contract module `drought`, spec §3; DB Module 1).

Sources (canonical, app/registry/datasets.yaml): SPI from `6.2`, SPEI from `meteorological_drought_master`, rainfall
from `6.1`; vegetation (NDVI, VCI, VHI) and soil moisture from `satellite_remote_sensing` (the D4 source shared with
the vegetation page); dam storage, inflow, outflow and SGI from `hydrological_drought_master`. The satellite and
hydrology tables end 2025-12, so every value carries its own `asOf` / `isStale` and combined panels list per-component
dates (`components`).

Methods (docs/DATA_MAPPING.md §2): area-weighted district means (≥ 95 % area coverage); drought class from the
aggregated SPI-6 via thresholds.json; area in drought = Σ district area per SPI-6 class; dam storage % = Σ volume /
Σ full-supply capacity, where each district's capacity is its (constant) volume / %FSC × 100; flows m³/s → Mm³/month.
Withheld (D5): SSI, river discharge, borehole level. Not in the DB: settlements, population, restrictions, forecasts.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

import pandas as pd

from app.core import districts
from app.core.aggregate import (
    AreaClass,
    area_class,
    class_areas,
    coverage_reason,
    short_of_coverage,
    sum_fractions,
    weighted,
    weighted_scalar,
    window_normal,
)
from app.core.data import DataAccess
from app.core.errors import DataUnavailable
from app.core.indices import calibration_gap, standardized_index
from app.core.palettes import DROUGHT, NORMAL, OBSERVED, SERIES, drought_bands
from app.core.provenance import Resolution, iso
from app.core.stats import rnd
from app.core.thresholds import (
    borehole_status,
    drought_class,
    drought_class_or_none,
    drought_label,
    severity_bar,
    storage_alert_level,
    storage_band,
    thresholds,
    veg_class,
)
from app.modules.common import Ctx, Selector, drought_status, forecast_unavailable, unknown
from app.modules.rainfall.service import HISTORY_START, MONTHS, WINDOW, RainfallService, _months
from app.modules.runoff.service import RunoffService
from app.schemas import contract as c

MODULE = "drought"
CLASSES = ["None", "D0", "D1", "D2", "D3", "D4"]
IN_DROUGHT = {"D1", "D2", "D3", "D4"}
CLIM_YEARS = (2001, 2025)  # satellite climatology (full years available)
SPI_SCALE = Selector("scale", ("1", "3", "6", "12", "24"), "3")  # SPI accumulation period, months
AREA_SCALE = Selector("scale", ("1", "3", "6", "12", "24"), "6")  # SPI used to classify the area in drought
AFFECTED_LEVEL = Selector("level", ("province", "catchment"), "province")
SELECTORS: dict[tuple[str, str], tuple[Selector, ...]] = {
    ("timeseries", "spi"): (SPI_SCALE,),
    ("timeseries", "drought-area"): (AREA_SCALE,),
    ("timeseries", "rain-anomaly"): (WINDOW,),
    ("table", "affected"): (AFFECTED_LEVEL,),
}


def unavailable(what: str, missing: str) -> DataUnavailable:
    return DataUnavailable(what, missing=[missing])


class DroughtService:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.data: DataAccess = ctx.data
        self.rain = RainfallService(ctx)

    # ---------------------------------------------------------------- helpers

    def src(self, *variables: str) -> list[str]:
        """Source tables of the variables that are served (withheld variables have no source)."""
        return self.rain.src(*(v for v in variables if self.withheld(v) is None))

    def withheld(self, var: str) -> str | None:
        """Why `var` is not served (its registry reason), or None when it has a canonical source."""
        reg = self.ctx.state.registry
        if reg.canonical_source(var) is not None:
            return None
        reasons = reg.withheld_reasons(var)
        label = reg.variables[var].label
        return f"{label} is withheld: " + (reasons[0].split(": ", 1)[-1] if reasons else "not in the database")

    def _prov(
        self, as_of: dt.date | None, *variables: str, baseline: bool = False, res: Resolution = "daily"
    ) -> dict[str, Any]:
        p = self.ctx.provenance(as_of, self.src(*variables), baseline=baseline, res=res)
        p.pop("asOf")
        return p

    def kpi_extra(self, as_of: dt.date | None, *variables: str, res: Resolution = "daily") -> dict[str, Any]:
        return {"as_of": iso(as_of), "source": self.src(*variables), **self.ctx.stamp(as_of, res)}

    async def latest(self, var: str) -> dt.date:
        """Latest month of a monthly variable (complete months only for monthly-from-daily rainfall indices)."""
        df = await self.data.monthly(var)
        last = _d(df["date"].max())
        if var.startswith(("spi_", "spei_")):
            return min(last, await self.rain.latest_complete_month())
        return last

    async def at(self, var: str, month: dt.date) -> pd.Series:
        df = await self.data.monthly(var)
        return df[df["date"] == pd.Timestamp(month)].set_index("district")["value"]

    async def spi6_classes(self, month: dt.date) -> pd.Series:
        return (await self.at("spi_6", month)).dropna().map(drought_class)

    def area_class(self, classes: pd.Series, w: dict[str, float]) -> AreaClass:
        """Scope drought class from the area in each class (headline rule, configurable %)."""
        return area_class(classes, w, self.ctx.state.settings.drought_class_area_pct)

    def mean_label(self, label: str) -> str:
        """Averaged indices say so on aggregated scopes (one district: the district's own value)."""
        return label if self.ctx.filters.scope.level == "municipality" else f"{label} (area-weighted mean)"

    async def capacities(self) -> pd.Series:
        """District full-supply capacity (Mm³) = volume / %FSC × 100 (constant per district; median over months)."""
        vol, pct = await self.data.monthly("dam_storage"), await self.data.monthly("dam_level_pct")
        m = vol.merge(pct, on=["district", "date"], suffixes=("_v", "_p"))
        m = m[m["value_p"] > 0.5]
        return (m["value_v"] / m["value_p"] * 100).groupby(m["district"]).median()

    async def storage_series(self) -> pd.DataFrame:
        """Scope storage % FSC and volume per month: Σ f·volume / Σ f·capacity."""
        f = sum_fractions(self.ctx.filters.scope, self.ctx.state.spatial)
        cap = await self.capacities()
        vol = await self.data.monthly("dam_storage")
        vol = vol[vol["district"].isin(f.keys()) & vol["district"].isin(cap.index)].copy()
        vol["f"] = vol["district"].map(f)
        vol["fv"] = vol["f"] * vol["value"]
        vol["fc"] = vol["f"] * vol["district"].map(cap)
        g = vol.groupby("date", as_index=False)[["fv", "fc"]].sum()
        g["pct"] = g["fv"] / g["fc"] * 100
        return g.rename(columns={"fv": "volume", "fc": "capacity"})

    async def flow_series(self, var: str) -> pd.Series:
        """Σ f·flow (m³/s) → Mm³ per month."""
        f = sum_fractions(self.ctx.filters.scope, self.ctx.state.spatial)
        df = await self.data.monthly(var)
        df = df[df["district"].isin(f.keys())].copy()
        secs = df["date"].map(lambda d: calendar.monthrange(d.year, d.month)[1] * 86400)
        df["mm3"] = df["district"].map(f) * df["value"] * secs / 1e6
        return df.groupby("date")["mm3"].sum()

    async def sm_years(self) -> str:
        """Climatology period of soil moisture actually used: CLIM_YEARS clipped to the column's valid record (the
        values before Mar 2015 are a constant fill and are never read)."""
        df = await self.data.monthly("soil_moisture_surface")
        years = df["date"].dt.year
        y0 = max(CLIM_YEARS[0], int(years.min())) if not df.empty else CLIM_YEARS[0]
        y1 = min(CLIM_YEARS[1], int(years.max())) if not df.empty else CLIM_YEARS[1]
        return f"{y0}–{y1}"

    async def sm_anomaly(self, month: dt.date) -> tuple[float | None, pd.Series]:
        """Surface soil-moisture anomaly (%) vs the same calendar month 2001–2025: scope value and per district."""
        w = self.ctx.weights()
        df = await self.data.monthly("soil_moisture_surface")
        clim = df[df["date"].dt.year.between(*CLIM_YEARS) & (df["date"].dt.month == month.month)]
        clim_d = clim.groupby("district")["value"].mean()
        now = df[df["date"] == pd.Timestamp(month)].set_index("district")["value"]
        per = (now - clim_d) / clim_d * 100
        cur, nor = weighted_scalar(now, w), weighted_scalar(clim_d, w)
        return ((cur - nor) / nor * 100 if cur is not None and nor else None), per

    async def first_last(self, last: dt.date) -> tuple[pd.Timestamp, pd.Timestamp]:
        """Window months for monthly series, clipped to the variable's last month."""
        start, _ = await self.rain.window()
        first = pd.Timestamp(start.replace(day=1))
        lt = pd.Timestamp(last)
        return (min(first, lt), lt)

    async def _monthly_series(self, var: str, last: dt.date, nd: int = 2) -> list[list[Any]]:
        first, lt = await self.first_last(last)
        df = await self.data.monthly(var)
        df = df[(df["date"] >= first) & (df["date"] <= lt)]
        agg = weighted(df, self.ctx.weights()).set_index("date")["value"]
        return [[iso(m.date()), rnd(agg.get(m), nd)] for m in _months(first.date(), lt.date())]

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        w = self.ctx.weights()
        kpis: list[c.Kpi] = []
        unavail: list[c.UnavailableField] = []

        m = await self.latest("spi_6")
        ac = self.area_class(await self.spi6_classes(m), w)
        cls = ac.cls
        thr = self.ctx.state.settings.drought_class_area_pct
        if cls is None:
            self.ctx.explain("kpis.class", f"No SPI-6 for districts covering ≥ 95 % of this scope's area in {m:%b %Y}")
        kpis.append(
            c.Kpi(
                id="class",
                metric="droughtClass",
                label="Drought Class",
                sublabel=(
                    f"{ac.in_drought:.0f} % of area in D1–D4 · worst class on ≥ {thr:g} % of the area "
                    f"(SPI-6, {m:%b %Y})"
                    if ac.in_drought is not None
                    else f"from district SPI-6 classes, {m:%b %Y}"
                ),
                value=float(thresholds()["drought"]["rank"][cls]) if cls else None,
                **({"text": ac.rank_text} if cls else {}),
                **({"status": c.Status(scale="drought", id=cls, label=drought_label(cls))} if cls else {}),
                **self.kpi_extra(m, "spi_6", res="monthly"),
            )
        )
        for kid, var, metric, label in (
            ("spi3", "spi_3", "spi", "SPI-3"),
            ("spi6", "spi_6", "spi", "SPI-6"),
            ("spei12", "spei_12", "spei", "SPEI-12"),
        ):
            mm = await self.latest(var)
            v = weighted_scalar(await self.at(var, mm), w)
            if v is None:
                self.ctx.explain(f"kpis.{kid}", f"No area-weighted {label} for this scope in {mm:%b %Y}")
            kpis.append(
                c.Kpi(
                    id=kid,
                    metric=metric,
                    label=self.mean_label(label),
                    sublabel=f"{mm:%b %Y}",
                    value=rnd(v, 2),
                    decimals=2,
                    **drought_status(v),
                    **self.kpi_extra(mm, var, res="monthly"),
                )
            )
        mv = await self.latest("vci")
        vci = weighted_scalar(await self.at("vci", mv), w)
        vci_kpi = c.Kpi(
            id="vci",
            metric="vci",
            label="VCI",
            sublabel=f"{mv:%b %Y}",
            value=rnd(vci, 1),
            unit="%",
            decimals=0,
            **self.kpi_extra(mv, "vci", res="monthly"),
        )
        if vci is not None:
            vci_kpi.status = c.Status(scale="vci", id=veg_class(vci), label=veg_class(vci))
        kpis.append(vci_kpi)
        ms = await self.latest("soil_moisture_surface")
        sma, _ = await self.sm_anomaly(ms)
        kpis.append(
            c.Kpi(
                id="soilMoisture",
                metric="soilMoistureAnomaly",
                label="Soil Moisture Anomaly",
                sublabel=f"surface, {ms:%b %Y} vs {await self.sm_years()}",
                value=rnd(sma, 1),
                unit="%",
                decimals=0,
                **self.kpi_extra(ms, "soil_moisture_surface", res="monthly"),
            )
        )
        # SSI: one rule on every page (B8) — computed from the scope's modelled monthly runoff depth (runoff page);
        # the database's SSI column is withheld (computed from placeholder discharge)
        ro = RunoffService(self.ctx)
        y1 = await ro.last_full_year()
        depth = await ro.scope_monthly_depth()
        lm = await ro.latest_complete_month()
        ssi = standardized_index(depth, 3, (HISTORY_START.year, y1)).get(pd.Timestamp(lm))
        sv = None if ssi is None or pd.isna(ssi) else float(ssi)
        if sv is None:
            gap = calibration_gap(depth, 3, (HISTORY_START.year, y1), "SSI-3")
            self.ctx.explain("kpis.ssi3", gap or f"No 3-month runoff total for this scope ending {lm:%b %Y}")
        kpis.append(
            c.Kpi(
                id="ssi3",
                metric="ssi",
                label="SSI-3 (modelled runoff)",
                sublabel=f"{lm:%b %Y}, gamma fit {HISTORY_START.year}–{y1}",
                value=rnd(sv, 2),
                decimals=2,
                **drought_status(sv),
                **self.kpi_extra(lm, "runoff_depth_daily", res="monthly"),
            )
        )

        reason_st = self.withheld("dam_storage")
        st = pd.DataFrame() if reason_st else await self.storage_series()
        if reason_st:
            kpis.append(
                c.Kpi(
                    id="storage",
                    metric="storagePct",
                    label="Dam Storage",
                    value=None,
                    unit="% FSC",
                    unavailable_reason=reason_st,
                )
            )
            unavail.append(c.UnavailableField(field="kpis.storage", reason=reason_st))
        elif not st.empty:
            last = st.iloc[st["date"].argmax()]
            prev = st[st["date"] == last["date"] - pd.DateOffset(years=1)]
            dmo = _d(last["date"])
            kpis.append(
                c.Kpi(
                    id="storage",
                    metric="storagePct",
                    label="Dam Storage",
                    sublabel=f"{rnd(last['volume'], 0):,.0f} of {rnd(last['capacity'], 0):,.0f} Mm³, {dmo:%b %Y}",
                    value=rnd(last["pct"], 1),
                    unit="% FSC",
                    decimals=1,
                    delta=rnd(last["pct"] - prev["pct"].iloc[0], 1) if not prev.empty else None,
                    delta_unit=" pp",
                    delta_label="vs last year",
                    spark=[x for x in (rnd(v, 1) for v in st.sort_values("date")["pct"].tail(12)) if x is not None],
                    **self.kpi_extra(dmo, "dam_storage", "dam_level_pct", res="monthly"),
                )
            )
        if reason_sgi := self.withheld("sgi"):
            kpis.append(c.Kpi(id="sgi", metric="sgi", label="SGI", value=None, unavailable_reason=reason_sgi))
            unavail.append(c.UnavailableField(field="kpis.sgi", reason=reason_sgi))
        else:
            msg = await self.latest("sgi")
            sgi = weighted_scalar(await self.at("sgi", msg), w)
            kpis.append(
                c.Kpi(
                    id="sgi",
                    metric="sgi",
                    label=self.mean_label("SGI"),
                    sublabel=f"{msg:%b %Y}",
                    value=rnd(sgi, 2),
                    decimals=2,
                    **self.kpi_extra(msg, "sgi", res="monthly"),
                )
            )
        reason_set = "No settlement register in the database"
        kpis.append(
            c.Kpi(
                id="settlements",
                metric="settlementsAtRisk",
                label="Settlements at Risk",
                value=None,
                unavailable_reason=reason_set,
            )
        )
        unavail.append(c.UnavailableField(field="kpis.settlements", reason=reason_set))

        dated = [k for k in kpis if k.as_of]
        as_of = max(dt.date.fromisoformat(k.as_of) for k in dated if k.as_of)
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
                for k in dated
            ],
            unavailable=unavail,
            **self._prov(
                as_of,
                "spi_6",
                "spei_12",
                "vci",
                "soil_moisture_surface",
                "runoff_depth_daily",
                "dam_storage",
                "sgi",
                res="monthly",
            ),
        )
        out = body.dump()
        out["isStale"] = any(k.is_stale for k in dated)  # some components are stale (satellite / hydrology)
        return out

    # ---------------------------------------------------------------- time series

    async def timeseries(self, metric: str) -> dict[str, Any]:
        handlers = {
            "spi": self.ts_spi,
            "rain-spei": self.ts_rain_spei,
            "rain-anomaly": self.ts_rain_anomaly,
            "dam-flows": self.ts_dam_flows,
            "gw-sgi": self.ts_gw_sgi,
            "soil-moisture": self.ts_soil_moisture,
            "vegetation": self.ts_vegetation,
            "drought-area": self.ts_drought_area,
        }
        not_in_db = {
            "storage-vs-spi": ("Storage by dam size needs per-dam data", "per-dam storage"),
            "population-at-risk": ("No population / settlement data in the database", "population at risk"),
            "recovery-province": ("Recovery probabilities need a forecast model", "forecast:recovery"),
        }
        if metric in not_in_db:
            raise unavailable(*not_in_db[metric])
        if metric not in handlers:
            raise unknown("metric", metric, [*handlers, *not_in_db])
        return (await handlers[metric]()).dump()

    async def ts_spi(self) -> c.SeriesSet:
        k = self.ctx.select(SPI_SCALE)
        var = f"spi_{k}"
        last = await self.latest(var)
        return c.SeriesSet(
            id="spi",
            title=f"SPI-{k}",
            x_type="time",
            axes=[c.Axis(unit="—", label=f"SPI-{k}")],
            series=[
                c.Series(
                    key=f"spi{k}",
                    name=f"SPI-{k}",
                    kind="bar" if k == "1" else "line",
                    color=OBSERVED,
                    data=await self._monthly_series(var, last),
                )
            ],
            bands=[c.Band.model_validate(b) for b in drought_bands()],
            as_of=iso(last),
            note=f"Area-weighted mean of district SPI-{k}; complete months only.",
            **self._prov(last, var, res="monthly"),
        )

    async def ts_rain_spei(self) -> c.SeriesSet:
        rain = await self.rain.ts_monthly_series()
        last = await self.latest("spei_3")
        return c.SeriesSet(
            id="rain-spei",
            title="Rainfall and SPEI-3",
            x_type="time",
            axes=[c.Axis(unit="mm", label="Rainfall"), c.Axis(unit="—", label="SPEI-3")],
            series=[
                c.Series(key="rain", name="Rainfall", kind="bar", axis=0, color=SERIES[0], data=rain["rain"]),
                c.Series(
                    key="normal",
                    name="Long-term mean (2000–2026)",
                    kind="line",
                    axis=0,
                    color=NORMAL,
                    dash=True,
                    data=rain["normal"],
                ),
                c.Series(
                    key="spei3",
                    name="SPEI-3",
                    kind="line",
                    axis=1,
                    color=SERIES[3],
                    data=await self._monthly_series("spei_3", last),
                ),
            ],
            as_of=iso(last),
            note="Normal: 2000–2026 mean (data start in 2000). Complete months only.",
            **self._prov(last, "rain_daily", "rain_anomaly_mm", "spei_3", baseline=True, res="monthly"),
        )

    async def ts_rain_anomaly(self) -> c.SeriesSet:
        s = await self.rain.ts_anomaly()
        return s.model_copy(update={"id": "rain-anomaly"})

    async def ts_dam_flows(self) -> c.SeriesSet:
        st = await self.storage_series()
        if st.empty:
            raise unavailable("No dam storage data for this scope", "dam storage")
        last = _d(st["date"].max())
        first, lt = await self.first_last(last)
        pct = st.set_index("date")["pct"]
        inflow, outflow = await self.flow_series("dam_inflow"), await self.flow_series("dam_outflow")
        ms = _months(first.date(), lt.date())
        return c.SeriesSet(
            id="dam-flows",
            title="Dam storage and flows",
            x_type="time",
            axes=[
                c.Axis(unit="%", label="Storage (% FSC)", min=0, max=100),
                c.Axis(unit="Mm³", label="Monthly volume"),
            ],
            series=[
                c.Series(
                    key="storage",
                    name="Storage (% FSC)",
                    kind="line",
                    axis=0,
                    color=OBSERVED,
                    data=[[iso(m.date()), rnd(pct.get(m), 1)] for m in ms],
                ),
                c.Series(
                    key="inflow",
                    name="Inflow",
                    kind="bar",
                    axis=1,
                    color=SERIES[0],
                    data=[[iso(m.date()), rnd(inflow.get(m), 1)] for m in ms],
                ),
                c.Series(
                    key="outflow",
                    name="Outflow",
                    kind="bar",
                    axis=1,
                    color=SERIES[3],
                    data=[[iso(m.date()), rnd(outflow.get(m), 1)] for m in ms],
                ),
            ],
            as_of=iso(last),
            note="District aggregates of dam storage (not individual dams); data end 2025-12.",
            **self._prov(last, "dam_storage", "dam_inflow", res="monthly"),
        )

    async def ts_gw_sgi(self) -> c.SeriesSet:
        last = await self.latest("sgi")
        sgi = await self._monthly_series("sgi", last)
        reason = "Borehole water level is withheld: values (−0.18 … +0.20) are not depths below ground"
        return c.SeriesSet(
            id="gw-sgi",
            title="Groundwater",
            x_type="time",
            axes=[c.Axis(unit="m bgl", label="Level", inverse=True), c.Axis(unit="—", label="SGI")],
            series=[
                c.Series(
                    key="level",
                    name="Mean borehole level (m bgl)",
                    kind="line",
                    axis=0,
                    color=SERIES[0],
                    data=[[x, None] for x, _ in sgi],
                ),
                c.Series(key="sgi", name="SGI", kind="line", axis=1, color=SERIES[3], data=sgi),
            ],
            as_of=iso(last),
            unavailable=[c.UnavailableField(field="series.level", reason=reason)],
            **self._prov(last, "sgi", res="monthly"),
        )

    async def ts_soil_moisture(self) -> c.SeriesSet:
        w = self.ctx.weights()
        last = await self.latest("soil_moisture_surface")
        first, lt = await self.first_last(last)
        surf = weighted(await self.data.monthly("soil_moisture_surface"), w).set_index("date")["value"]
        root = weighted(await self.data.monthly("soil_moisture_rootzone"), w).set_index("date")["value"]
        clim = surf[(surf.index.year >= CLIM_YEARS[0]) & (surf.index.year <= CLIM_YEARS[1])]
        by_m = clim.groupby(clim.index.month).agg(["min", "max", "mean"])
        ms = _months(first.date(), lt.date())
        return c.SeriesSet(
            id="soil-moisture",
            title="Soil moisture",
            x_type="time",
            axes=[c.Axis(unit="m³/m³", label="Soil moisture")],
            series=[
                c.Series(
                    key="range",
                    name="Min–max range",
                    kind="band",
                    color="#C7EAE5",
                    data=[
                        [iso(m.date()), rnd(by_m["min"].get(m.month), 3), rnd(by_m["max"].get(m.month), 3)] for m in ms
                    ],
                ),
                c.Series(
                    key="surface",
                    name="Surface soil moisture",
                    kind="line",
                    color=SERIES[0],
                    data=[[iso(m.date()), rnd(surf.get(m), 3)] for m in ms],
                ),
                c.Series(
                    key="rootzone",
                    name="Root-zone soil moisture",
                    kind="line",
                    color=SERIES[1],
                    data=[[iso(m.date()), rnd(root.get(m), 3)] for m in ms],
                ),
                c.Series(
                    key="mean",
                    name="Long-term mean (surface)",
                    kind="line",
                    color=NORMAL,
                    dash=True,
                    data=[[iso(m.date()), rnd(by_m["mean"].get(m.month), 3)] for m in ms],
                ),
            ],
            as_of=iso(last),
            note=f"Range and mean per calendar month over {await self.sm_years()} (the satellite soil-moisture record "
            "starts in Mar 2015). Data end 2025-12.",
            **{
                **self._prov(last, "soil_moisture_surface", "soil_moisture_rootzone", res="monthly"),
                "baseline": f"{await self.sm_years()} monthly range",
                "baselineNote": self.ctx.state.registry.baseline.note,
            },
        )

    async def ts_vegetation(self) -> c.SeriesSet:
        last = await self.latest("ndvi")
        return c.SeriesSet(
            id="vegetation",
            title="Vegetation condition",
            x_type="time",
            axes=[c.Axis(unit="—", label="NDVI", min=0, max=1), c.Axis(unit="%", label="VCI / VHI", min=0, max=100)],
            series=[
                c.Series(
                    key="ndvi",
                    name="NDVI",
                    kind="line",
                    axis=0,
                    color=SERIES[1],
                    data=await self._monthly_series("ndvi", last, 3),
                ),
                c.Series(
                    key="vci",
                    name="VCI",
                    kind="line",
                    axis=1,
                    color=SERIES[0],
                    data=await self._monthly_series("vci", last, 1),
                ),
                c.Series(
                    key="vhi",
                    name="VHI",
                    kind="line",
                    axis=1,
                    color=SERIES[2],
                    data=await self._monthly_series("vhi", last, 1),
                ),
            ],
            as_of=iso(last),
            note="satellite_remote_sensing (single vegetation source for the drought and vegetation pages); "
            "data end 2025-12.",
            **self._prov(last, "ndvi", "vci", "vhi", res="monthly"),
        )

    async def ts_drought_area(self) -> c.SeriesSet:
        k = self.ctx.select(AREA_SCALE)
        var = f"spi_{k}"
        w = self.ctx.weights()
        last = await self.latest(var)
        first, lt = await self.first_last(last)
        df = await self.data.monthly(var)
        df = df[(df["date"] >= first) & (df["date"] <= lt)]
        total = sum(w.values())
        rows: dict[pd.Timestamp, dict[str, float]] = {}
        for m, g in df.groupby("date"):
            areas = class_areas(g.set_index("district")["value"].map(drought_class), w)
            covered = sum(areas.values())
            rows[m] = {k: v / total * 100 for k, v in areas.items()} if covered >= 0.95 * total else {}
        ms = _months(first.date(), lt.date())
        series = [
            c.Series(
                key=k,
                name=k,
                kind="area",
                stack="area",
                color=DROUGHT[k],
                data=[[iso(m.date()), (rnd(rows.get(m, {}).get(k, 0.0), 1) if rows.get(m) else None)] for m in ms],
            )
            for k in ("D0", "D1", "D2", "D3", "D4")
        ]
        return c.SeriesSet(
            id="drought-area",
            title=f"Area in drought (SPI-{k} classes)",
            x_type="time",
            axes=[c.Axis(unit="%", label="Area", min=0, max=100)],
            series=series,
            as_of=iso(last),
            note=f"Share of the scope's area whose district SPI-{k} falls in each class.",
            **self._prov(last, var, res="monthly"),
        )

    # ---------------------------------------------------------------- forecast

    async def forecast(self, metric: str) -> dict[str, Any]:
        allowed = ["spi", "area", "settlements", "population"]
        if metric not in allowed:
            raise unknown("metric", metric, allowed)
        raise forecast_unavailable(MODULE, metric)

    # ---------------------------------------------------------------- breakdown

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric in ("restrictions", "class-probability"):
            raise unavailable(
                "Water restrictions are not in the database"
                if metric == "restrictions"
                else "Class probabilities need a forecast model",
                metric,
            )
        m = await self.latest("spi_6")
        classes = await self.spi6_classes(m)
        if metric == "severity":
            w = self.ctx.weights()
            areas = class_areas(classes, w)
            vals: list[float | None] = [rnd(areas.get(k, 0.0), 0) for k in CLASSES]
            in_d = sum(areas.get(k, 0.0) for k in IN_DROUGHT)
            return c.CategorySet(
                id="severity",
                title=f"Area by drought class, {m:%b %Y}",
                unit="km²",
                categories=[drought_label(k) for k in CLASSES],
                colors=[DROUGHT[k] for k in CLASSES],
                series=[c.CategorySeries(key="area", name="Area", values=vals)],
                center=c.Center(value=f"{in_d / sum(w.values()) * 100:.0f}%", label="in drought (D1–D4)"),
                as_of=iso(m),
                **self._prov(m, "spi_6", res="monthly"),
            ).dump()
        if metric == "province-area":
            areas_all = self.ctx.state.spatial.district_areas()
            if areas_all is None:
                raise unavailable("Province areas need district boundaries", "district boundaries")
            groups = {
                districts.PROVINCES[p]: {d.code: areas_all[d.code] for d in districts.in_province(p)}
                for p in districts.PROVINCES
            }
            if short := short_of_coverage(set(classes.dropna().index), groups):
                self.ctx.explain("series.area", coverage_reason(f"SPI-6 for {m:%b %Y}", short, len(groups)))
            vals = []
            for p in districts.PROVINCES:
                w = {d.code: areas_all[d.code] for d in districts.in_province(p)}
                a = class_areas(classes, w)
                vals.append(
                    rnd(sum(a.get(k, 0.0) for k in IN_DROUGHT) / sum(w.values()) * 100, 1)
                    if sum(a.values()) >= 0.95 * sum(w.values())
                    else None
                )
            return c.CategorySet(
                id="province-area",
                title=f"Area in drought by province, {m:%b %Y}",
                unit="%",
                categories=list(districts.PROVINCES.values()),
                series=[c.CategorySeries(key="area", name="Area in drought (D1–D4)", values=vals)],
                as_of=iso(m),
                **{**self._prov(m, "spi_6", res="monthly"), "aggregation": "district-area-weighted"},
            ).dump()
        raise unknown("metric", metric, ["severity", "province-area", "restrictions", "class-probability"])

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric in ("early-warning", "forecast-indicators", "recovery", "forecast-kpis"):
            raise forecast_unavailable(MODULE, metric)
        handlers = {
            "cascade": self.tbl_cascade,
            "affected": self.tbl_affected,
            "sources": self.tbl_sources,
            "municipalities": self.tbl_municipalities,
        }
        if metric not in handlers:
            raise unknown(
                "metric", metric, [*handlers, "early-warning", "forecast-indicators", "recovery", "forecast-kpis"]
            )
        return (await handlers[metric](limit)).dump()

    async def tbl_cascade(self, limit: int | None) -> c.TableSet:
        """The spec's 7-stage drought cascade (§3, §25.1): precipitation deficit → soil moisture → vegetation →
        streamflow → reservoir → groundwater → socio-economic impact.

        Rows carry `stageId` and `order` (1–7), the backend's keys (`stage`, `value`, `class`, `severity`, `asOf`) and
        the contract's cascade keys (`metric`, `value` as text with its unit, `cls` = class id on the stage's palette
        `scale`, `bar` = 0–100 from the class rank). The number is `valueNum`. Row ids of the original four stages are
        kept (`meteorological`, `agricultural`, `hydrological` = reservoir, `socio-economic`). Stages the database
        cannot fill are null with the reason: streamflow (no gauged discharge), reservoir (storage withheld after the
        plausibility check), socio-economic (no settlement data).
        """
        w = self.ctx.weights()
        total = sum(w.values())
        unavailable: list[c.UnavailableField] = []
        components: list[c.Component] = []
        stress = thresholds()["vegetation"]["stress_below"]

        def stage(
            order: int,
            rid: str,
            stage_id: str,
            label: str,
            metric: str,
            scale: str,
            num: float | None,
            text: str | None,
            cls_id: str | None,
            *,
            cls_label: str | None = None,
            severity: float | None = None,
            as_of: dt.date | None = None,
        ) -> dict[str, Any]:
            return {
                "id": rid,
                "stageId": stage_id,
                "order": order,
                "stage": label,
                "metric": metric,
                "value": text if num is not None else None,
                "valueNum": num,
                "cls": ("No drought" if cls_id == "None" else cls_id) if cls_id else None,
                "scale": scale,
                "bar": severity_bar(scale, cls_id),
                "class": cls_label,
                "severity": rnd(severity, 0),
                "asOf": iso(as_of),
            }

        def component(key: str, as_of: dt.date, *variables: str) -> None:
            components.append(
                c.Component(key=key, as_of=iso(as_of), source=self.src(*variables), **self.ctx.stamp(as_of, "monthly"))
            )

        rows: list[dict[str, Any]] = []
        # 1 precipitation deficit: class by the area rule (as the Drought Class KPI), value = area-weighted mean SPI-6
        m = await self.latest("spi_6")
        spi = weighted_scalar(await self.at("spi_6", m), w)
        ac = self.area_class(await self.spi6_classes(m), w)
        rows.append(
            stage(
                1,
                "meteorological",
                "precipitation",
                "Precipitation deficit (SPI-6)",
                "spi",
                "drought",
                rnd(spi, 2),
                f"SPI-6 {spi:.2f}" if spi is not None else None,
                ac.cls,
                cls_label=drought_label(ac.cls) if ac.cls else None,
                severity=ac.in_drought,
                as_of=m,
            )
        )
        component("meteorological", m, "spi_6")
        # 2 soil moisture: surface soil-moisture anomaly vs the same month 2001–2025 (no agreed classes)
        ms = await self.latest("soil_moisture_surface")
        sma, _ = await self.sm_anomaly(ms)
        rows.append(
            stage(
                2,
                "soil-moisture",
                "soil-moisture",
                "Soil moisture (surface, anomaly)",
                "soilMoistureAnomaly",
                "anomalyPct",
                rnd(sma, 1),
                f"{sma:+.0f} % vs {await self.sm_years()}" if sma is not None else None,
                None,
                as_of=ms,
            )
        )
        no_sm_classes = "No agreed soil-moisture anomaly classes yet: the anomaly is shown without a class"
        if sma is not None:
            for key in ("cls", "bar", "class", "severity"):
                unavailable.append(c.UnavailableField(field=f"rows.soil-moisture.{key}", reason=no_sm_classes))
        component("soil-moisture", ms, "soil_moisture_surface")
        # 3 vegetation: VHI class; severity = % of area under stress
        mv = await self.latest("vhi")
        vhi_d = (await self.at("vhi", mv)).dropna()
        vhi = weighted_scalar(vhi_d, w)
        veg_cls = veg_class(vhi) if vhi is not None else None
        stressed = sum(w[k] for k, v in vhi_d.items() if k in w and v < stress)
        rows.append(
            stage(
                3,
                "agricultural",
                "vegetation",
                "Vegetation (VHI %)",
                "vhi",
                "vci",
                rnd(vhi, 1),
                f"VHI {vhi:.0f} %" if vhi is not None else None,
                veg_cls,
                cls_label=veg_cls,
                severity=stressed / total * 100 if vhi is not None else None,
                as_of=mv,
            )
        )
        component("agricultural", mv, "vhi")
        # 4 streamflow: no gauged discharge in the database
        rows.append(stage(4, "streamflow", "streamflow", "Streamflow", "discharge", "drought", None, None, None))
        unavailable.append(
            c.UnavailableField(
                field="rows.streamflow",
                reason="No gauged streamflow in the database: the river discharge column holds the same placeholder "
                "value in every district. The SSI shown on this page is computed from modelled runoff, not streamflow.",
            )
        )
        # 5 reservoir: dam storage (withheld after the plausibility check)
        reason_st = self.withheld("dam_storage")
        st = pd.DataFrame() if reason_st else await self.storage_series()
        if not st.empty:
            last = st.iloc[st["date"].argmax()]
            ds = _d(last["date"])
            pct = float(last["pct"])
            lvl = storage_alert_level(pct)
            rows.append(
                stage(
                    5,
                    "hydrological",
                    "reservoir",
                    "Reservoir (dam storage % FSC)",
                    "storagePct",
                    "storage",
                    rnd(pct, 1),
                    f"{pct:.1f} % FSC",
                    storage_band(pct),
                    cls_label="No alert" if lvl == "None" else f"Alert {lvl}",
                    severity=100 - pct,
                    as_of=ds,
                )
            )
            component("hydrological", ds, "dam_storage")
        else:
            rows.append(
                stage(
                    5,
                    "hydrological",
                    "reservoir",
                    "Reservoir (dam storage % FSC)",
                    "storagePct",
                    "storage",
                    None,
                    None,
                    None,
                )
            )
            unavailable.append(
                c.UnavailableField(field="rows.hydrological", reason=reason_st or "No dam storage data for this scope")
            )
        # 6 groundwater: SGI with the spec's borehole status classes; severity = % of area at Low or worse (≤ −1.0)
        if reason_sgi := self.withheld("sgi"):
            rows.append(stage(6, "groundwater", "groundwater", "Groundwater (SGI)", "sgi", "status", None, None, None))
            unavailable.append(c.UnavailableField(field="rows.groundwater", reason=reason_sgi))
        else:
            mg = await self.latest("sgi")
            sgi_d = (await self.at("sgi", mg)).dropna()
            sgi = weighted_scalar(sgi_d, w)
            gw_cls = borehole_status(sgi) if sgi is not None else None
            low = sum(w[k] for k, v in sgi_d.items() if k in w and borehole_status(float(v)) in ("Low", "Critical"))
            covered = sum(w[k] for k in sgi_d.index if k in w)
            rows.append(
                stage(
                    6,
                    "groundwater",
                    "groundwater",
                    "Groundwater (SGI)",
                    "sgi",
                    "status",
                    rnd(sgi, 2),
                    f"SGI {sgi:.2f}" if sgi is not None else None,
                    gw_cls,
                    cls_label=gw_cls,
                    severity=low / covered * 100 if sgi is not None and covered else None,
                    as_of=mg,
                )
            )
            component("groundwater", mg, "sgi")
        # 7 socio-economic impact
        rows.append(
            stage(
                7,
                "socio-economic",
                "socio-economic",
                "Socio-economic (settlements at risk)",
                "settlementsAtRisk",
                "risk",
                None,
                None,
                None,
            )
        )
        unavailable.append(
            c.UnavailableField(field="rows.socio-economic", reason="No settlement / population data in the database")
        )
        explained = {u.field for u in unavailable}
        for r in rows:  # a stage without a value for this scope says why
            if r["valueNum"] is None and f"rows.{r['id']}" not in explained:
                self.ctx.explain(f"rows.{r['id']}", f"No area-weighted value for this scope ({r['stage']})")
        as_of = max(dt.date.fromisoformat(x["asOf"]) for x in rows if x["asOf"])
        return c.TableSet(
            id="cascade",
            title="Drought impact cascade",
            columns=[
                c.Column(key="stage", label="Stage", type="text"),
                c.Column(key="value", label="Value", type="text", align="right"),
                c.Column(key="class", label="Class", type="text"),
                c.Column(key="severity", label="Severity", type="bar", unit="%", decimals=0),
                c.Column(key="asOf", label="As of", type="date"),
                c.Column(key="cls", label="Class", type="class"),
                c.Column(key="bar", label="Severity (class)", type="bar", unit="%", decimals=0),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(as_of),
            components=components,
            unavailable=unavailable,
            **{
                **self._prov(as_of, "spi_6", "soil_moisture_surface", "vhi", "dam_storage", "sgi", res="monthly"),
                "isStale": any(comp.is_stale for comp in components),
            },
        )

    async def tbl_affected(self, limit: int | None) -> c.TableSet:
        if self.ctx.select(AFFECTED_LEVEL) == "catchment":
            raise unavailable(
                "Drought by catchment is not available: the database is per district, and districts are far coarser "
                "than catchments (no catchment data or boundaries)",
                "catchment",
            )
        areas = self.ctx.state.spatial.district_areas()
        if areas is None:
            raise unavailable("Province areas need district boundaries", "district boundaries")
        m = await self.latest("spi_6")
        spi = await self.at("spi_6", m)
        classes = spi.dropna().map(drought_class)
        groups = {
            districts.PROVINCES[p]: {d.code: areas[d.code] for d in districts.in_province(p)}
            for p in districts.PROVINCES
        }
        if short := short_of_coverage(set(classes.index), groups):
            reason = coverage_reason(f"SPI-6 for {m:%b %Y}", short, len(groups))
            for col in ("spi6", "class", "area"):
                self.ctx.explain(f"rows.{col}", reason)
        rows = []
        for p in districts.PROVINCES:
            w = {d.code: areas[d.code] for d in districts.in_province(p)}
            v = weighted_scalar(spi, w)
            a = class_areas(classes, w)
            covered = sum(a.values()) >= 0.95 * sum(w.values())
            rows.append(
                {
                    "id": p,
                    "province": districts.PROVINCES[p],
                    "spi6": rnd(v, 2),
                    "class": self.area_class(classes, w).cls,  # area rule, not the class of the mean SPI-6
                    "area": rnd(sum(a.get(k, 0.0) for k in IN_DROUGHT) / sum(w.values()) * 100, 0) if covered else None,
                    "confidence": None,
                    "updated": iso(m),
                }
            )
        rows.sort(key=lambda r: r["spi6"] if r["spi6"] is not None else 99)
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return c.TableSet(
            id="affected",
            title=f"Drought-affected provinces, {m:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank", align="center"),
                c.Column(key="province", label="Province", type="text"),
                c.Column(
                    key="spi6",
                    label="SPI-6 (area-weighted mean)",
                    type="number",
                    decimals=2,
                    scale="spi",
                    align="right",
                ),
                c.Column(key="class", label="Drought class", type="class", scale="drought"),
                c.Column(key="area", label="Area affected", type="bar", unit="%", decimals=0),
                c.Column(key="confidence", label="Confidence", type="text"),
                c.Column(key="updated", label="Last update", type="date"),
            ],
            rows=rows[:limit] if limit else rows,
            row_key="id",
            total=len(rows),
            as_of=iso(m),
            unavailable=[
                c.UnavailableField(field="rows.confidence", reason="No forecast / ensemble data to estimate confidence")
            ],
            **{**self._prov(m, "spi_6", res="monthly"), "aggregation": "district-area-weighted"},
        )

    async def tbl_municipalities(self, limit: int | None) -> c.TableSet:
        w = self.ctx.weights()
        m = await self.latest("spi_6")
        spi = (await self.at("spi_6", m)).dropna()
        spi = spi[spi.index.isin(w)].sort_values()
        rows = []
        for i, (code, v) in enumerate(spi.items(), 1):
            d = districts.by_code(str(code))
            assert d is not None
            rows.append(
                {
                    "rank": i,
                    "id": d.code,
                    "municipality": d.official_name,
                    "province": d.province,
                    "spi6": rnd(float(v), 2),
                    "class": drought_class(float(v)),
                    "population": None,
                    "risk": None,
                }
            )
        reason = "No population or water-supply data in the database"
        return c.TableSet(
            id="municipalities",
            title=f"Districts by SPI-6, {m:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank", align="center"),
                c.Column(key="municipality", label="Municipality", type="text"),
                c.Column(key="province", label="Province", type="text"),
                c.Column(key="spi6", label="SPI-6", type="number", decimals=2, scale="spi", align="right"),
                c.Column(key="class", label="Drought class", type="class", scale="drought"),
                c.Column(key="population", label="Population affected", type="number", decimals=0, align="right"),
                c.Column(key="risk", label="Water supply risk", type="class", scale="risk"),
            ],
            rows=rows[: limit or 20],
            row_key="id",
            total=len(rows),
            as_of=iso(m),
            unavailable=[
                c.UnavailableField(field="rows.population", reason=reason),
                c.UnavailableField(field="rows.risk", reason=reason),
            ],
            **{**self._prov(m, "spi_6", res="monthly"), "aggregation": "district"},
        )

    async def tbl_sources(self, limit: int | None) -> c.TableSet:
        meta = await self.ctx.state.meta.sources()
        mine = {ds.table for ds in self.ctx.state.registry.datasets_for_module(MODULE)}
        rows = []
        for it in meta["items"]:
            if it["table"] not in mine:
                continue
            status = "Offline" if not it["exists"] else ("Delayed" if it["isStale"] else "Online")
            rows.append(
                {
                    "id": it["table"],
                    "source": it["table"],
                    "feed": f"{it['resolution']}, 52 districts",
                    "status": status,
                    "updated": it["asOf"],
                }
            )
        return c.TableSet(
            id="sources",
            title="Drought data sources",
            columns=[
                c.Column(key="source", label="Source", type="text"),
                c.Column(key="feed", label="Feed", type="text"),
                c.Column(key="status", label="Status", type="class", scale="feed"),
                c.Column(key="updated", label="Last updated", type="date"),
            ],
            rows=rows,
            row_key="id",
            as_of=meta["newestDataDate"],
            source=sorted(mine),
            is_stale=any(r["status"] != "Online" for r in rows),
        )

    # ---------------------------------------------------------------- matrix

    async def matrix(self, metric: str) -> dict[str, Any]:
        if metric == "province-risk":
            raise unavailable("Province risk probabilities need a forecast model", "forecast:province-risk")
        if metric != "veg-heatmap":
            raise unknown("metric", metric, ["veg-heatmap", "province-risk"])
        areas = self.ctx.state.spatial.district_areas()
        if areas is None:
            raise unavailable("Province areas need district boundaries", "district boundaries")
        last = await self.latest("vci")
        months = _months(_d(pd.Timestamp(last) - pd.DateOffset(months=11)), last)
        df = await self.data.monthly("vci")
        groups = {
            districts.PROVINCES[p]: {d.code: areas[d.code] for d in districts.in_province(p)}
            for p in districts.PROVINCES
        }
        present = set(df[df["date"].isin(months) & df["value"].notna()]["district"])
        if short := short_of_coverage(present, groups):
            self.ctx.explain("values", coverage_reason("VCI in the last 12 months", short, len(groups)))
        values = []
        for p in districts.PROVINCES:
            w = {d.code: areas[d.code] for d in districts.in_province(p)}
            agg = weighted(df[df["date"].isin(months)], w).set_index("date")["value"]
            values.append([rnd(agg.get(m), 1) for m in months])
        return c.MatrixSet(
            id="veg-heatmap",
            title="VCI by province, last 12 months",
            rows=list(districts.PROVINCES.values()),
            cols=[f"{MONTHS[m.month - 1]} {m.year}" for m in months],
            values=values,
            unit="%",
            scale="vci",
            as_of=iso(last),
            **{**self._prov(last, "vci", res="monthly"), "aggregation": "district-area-weighted"},
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        not_available = {
            "catchment-class": ("Catchment classes are not available: data are per district", "catchment-level data"),
            "dams": ("No dam locations in the database (district aggregates only)", "dam coordinates"),
            "boreholes": ("No borehole locations in the database", "borehole coordinates"),
            "settlements": ("No settlement register in the database", "settlements"),
        }
        if layer in not_available:
            raise unavailable(*not_available[layer])
        if layer in ("outlook-1", "outlook-2", "outlook-3", "forecast-6"):
            raise forecast_unavailable(MODULE, layer)
        w = self.ctx.weights()
        index_layers = {
            "spi1": "spi_1",
            "spi3": "spi_3",
            "spi6": "spi_6",
            "spi12": "spi_12",
            "spi24": "spi_24",
            "spei3": "spei_3",
            "spei12": "spei_12",
            "drought-class": "spi_6",
        }
        sat_layers = {
            "soil-moisture": ("soil_moisture_surface", "Surface soil moisture", "soilMoisture", "m³/m³", 3),
            "ndvi": ("ndvi", "NDVI", "ndvi", "—", 3),
            "vci": ("vci", "Vegetation Condition Index", "vci", "%", 1),
        }
        level = "municipality"
        baseline = False
        if layer in index_layers:
            var = index_layers[layer]
            m = await self.latest(var)
            vals = (await self.at(var, m)).map(lambda v: rnd(v, 2))
            title = (
                "Drought classification (SPI-6)"
                if layer == "drought-class"
                else self.ctx.state.registry.variables[var].label
            ) + f" – {m:%b %Y}"
            scale, unit, date, variables = "spi", "—", m, [var]
        elif layer in sat_layers:
            var, label, scale, unit, nd = sat_layers[layer]
            m = await self.latest(var)
            vals = (await self.at(var, m)).map(lambda v: rnd(v, nd))
            title, date, variables = f"{label} – {m:%b %Y}", m, [var]
        elif layer in ("municipal-class", "province-class"):
            m = await self.latest("spi_6")
            spi = await self.at("spi_6", m)
            scale, unit, date, variables = "drought", "—", m, ["spi_6"]
            if layer == "municipal-class":
                vals = spi.map(drought_class_or_none)
                title = f"Drought classification by district (SPI-6) – {m:%b %Y}"
            else:
                areas = self.ctx.state.spatial.district_areas()
                if areas is None:
                    raise unavailable("Province classes need district boundaries", "district boundaries")
                groups = {
                    districts.PROVINCES[p]: {d.code: areas[d.code] for d in districts.in_province(p)}
                    for p in districts.PROVINCES
                }
                if short := short_of_coverage(set(spi.dropna().index), groups):
                    self.ctx.explain("values", coverage_reason(f"SPI-6 for {m:%b %Y}", short, len(groups)))
                classes = spi.dropna().map(drought_class)
                vals = pd.Series(
                    {
                        p: self.area_class(classes, {d.code: areas[d.code] for d in districts.in_province(p)}).cls
                        for p in districts.PROVINCES
                    }
                )
                level, title = "province", f"Drought classification by province (SPI-6 area rule) – {m:%b %Y}"
        elif layer == "rain-anomaly":
            newest = await self.rain.newest_daily()
            start = _d(pd.Timestamp(newest) - pd.DateOffset(months=3) + pd.Timedelta(days=1))
            tot = await self.rain.window_totals(start, newest)
            n = window_normal(await self.rain.normals(), start, newest)
            vals = ((tot - n) / n * 100).map(lambda v: rnd(v, 1))
            title = f"Rainfall anomaly, 3 months to {newest:%d %b %Y} (% of normal)"
            scale, unit, date, variables, baseline = "anomalyPct", "%", newest, ["rain_daily", "rain_anomaly_mm"], True
        else:
            raise unknown(
                "layer",
                layer,
                [
                    *index_layers,
                    *sat_layers,
                    "municipal-class",
                    "province-class",
                    "rain-anomaly",
                    *not_available,
                    "outlook-1",
                    "outlook-2",
                    "outlook-3",
                    "forecast-6",
                ],
            )
        if level == "municipality":
            values = {str(k): v for k, v in vals.items() if k in w}
        else:
            sel = self.ctx.filters.scope
            keep = {districts.by_code(x).province for x in sel.district_codes if districts.by_code(x)}  # type: ignore[union-attr]
            values = {str(k): v for k, v in vals.items() if k in keep}
        prov = self.ctx.provenance(
            date, self.src(*variables), baseline=baseline, res="daily" if layer == "rain-anomaly" else "monthly"
        )
        prov["aggregation"] = "district" if level == "municipality" else "district-area-weighted"
        return c.MapLayer(
            id=layer,
            title=title,
            kind="choropleth",
            level=level,
            scale=scale,
            unit=unit,
            date=iso(date) or "",
            values=values,
            geometry="districts" if level == "municipality" else "provinces",
            **prov,
        ).dump()


def _d(ts: Any) -> dt.date:
    d: dt.date = pd.Timestamp(ts).date()
    return d


async def prewarm(data: DataAccess) -> None:
    for var in (
        "spi_3",
        "spi_6",
        "spei_12",
        "vci",
        "vhi",
        "ndvi",
        "soil_moisture_surface",
    ):
        await data.monthly(var)
