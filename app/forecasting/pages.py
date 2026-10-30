"""Precomputed forecasts for the module pages (DECISIONS D37, D41).

At startup, and again whenever the newest date in the database changes, an **Auto** run (24 months) is made for each
page's key variables, for South Africa and each province (national first); then, with the quick models (`auto-fast`),
the outlook variables (SPI-3, SPI-6, rainfall, NDVI, VCI, WQI) for every district, which feed the district outlook
maps. Any other (variable, area) pair — a district's SPEI-3, a WMA's runoff — is computed with the quick models the
first time its forecast tab is opened (on demand) and kept until the data change.

They are served by the contract forecast endpoints: forecast series (observed + median + 10–90 % band, the divider at
the last observed month), indicator tables (current value and 3 / 6 / 12 / 24 months ahead), class probabilities by
lead time (SPI classes, WQI bands), the province × lead-time probability of D2+, outlook maps per district, flood
(exceedance) probabilities, early-warning and recovery tables. Each carries `forecast` (model, skill, data date,
disclaimer).

Products whose data the database does not hold stay 501: settlement / population counts, reservoir storage (withheld,
D2), water demand, climate scenarios.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from app.core import districts
from app.core.aggregate import sum_fractions
from app.core.errors import Busy, DataUnavailable
from app.core.filters import ScopeSel, parse_scope
from app.core.palettes import SERIES
from app.core.provenance import iso
from app.core.stats import rnd
from app.core.thresholds import drought_class, drought_label, thresholds
from app.forecasting.base import Z90
from app.forecasting.jobs import ForecastJobs
from app.forecasting.registry import FAST
from app.forecasting.series import (
    Target,
    _combine,
    context,
    district_frame,
    district_scope,
    target_from_catalogue,
)
from app.forecasting.validation import SeriesError
from app.modules.categories import _value_sql
from app.modules.common import Ctx
from app.registry.registry import Registry
from app.schemas import contract as c
from app.state import AppState

log = logging.getLogger("niwis.forecast.pages")
PAGE_HORIZON = 24
HORIZONS = (3, 6, 12, 24)
HISTORY_SHOWN = 36  # months of observations drawn before the forecast
PROVINCES = ("EC", "FS", "GP", "KZN", "LP", "MP", "NC", "NW", "WC")
BAND, OBS, MEDIAN = "#90CAF9", "#13233A", SERIES[0]
D_CLASSES = ("None", "D0", "D1", "D2", "D3", "D4")
CLASS_COLORS = {"None": "#9E9E9E", "D0": "#FFFF00", "D1": "#FCD37F", "D2": "#FFAA00", "D3": "#E60000", "D4": "#730000"}
WQI_CLASSES = ("Excellent", "Good", "Fair", "Poor", "Very Poor")
WQI_COLORS = {"Excellent": "#1565C0", "Good": "#2E9E5B", "Fair": "#F6CF3E", "Poor": "#F28C28", "Very Poor": "#B2182B"}


@dataclass(frozen=True)
class PageVar:
    id: str
    label: str  # chart / table label
    axis: str  # y-axis label
    make: Callable[[Registry], Target]
    decimals: int = 2
    pages: tuple[str, ...] = ()


def _col(table: str, column: str, **kw: Any) -> Callable[[Registry], Target]:
    return lambda reg: replace(target_from_catalogue(table, column), **kw)


def _d1_area(reg: Registry) -> Target:
    """% of the scope's area in D1–D4 by district SPI-6 (the drought page's area chart, SPI-6 classes)."""
    table, column = "6.2_rainfall_derived_drought_indices_master", "SPI_6_Medium_Term_Hydrological_Impacts"
    ds = reg.datasets[table]
    v = _value_sql(ds, column)
    d1 = next(float(x["max"]) for x in thresholds()["drought"]["classes"] if x["id"] == "D1")
    expr = f"CASE WHEN round(({v})::numeric, 2) <= {d1!r} THEN 100.0 WHEN ({v}) IS NOT NULL THEN 0.0 END"
    base = target_from_catalogue(table, column)
    return replace(
        base,
        label="Area in drought (D1–D4, SPI-6)",
        unit="%",
        expr=expr,
        expr_id="d1plus-area",
        classify=False,
        anomaly=False,
        note="Share of the area whose district SPI-6 is D1 or worse",
    )


def _hot_days(reg: Registry) -> Target:
    table, column = "4_1_temperature_change_master", "Tmax_C"
    v = _value_sql(reg.datasets[table], column)
    base = target_from_catalogue(table, column)
    return replace(
        base,
        label="Hot days (Tmax > 35 °C)",
        unit="days/month",
        aggregate="sum",
        native_unit="days",
        expr=f"CASE WHEN ({v}) > 35 THEN 1.0 WHEN ({v}) IS NOT NULL THEN 0.0 END",
        expr_id="hot35",
        note="Days per month with a district Tmax above 35 °C",
    )


def _runoff_depth(reg: Registry) -> Target:
    table, column = "runoff_variables_master", "Daily_Surface_Runoff_Volume_m3_day"
    ds = reg.datasets[table]
    return Target(
        table=table,
        column=column,
        label="Runoff depth",
        unit="mm/month",
        resolution="daily",
        aggregate="sum",
        extensive=False,
        expr=ds.column_sql(column),
        expr_id="depth-mm",
        native_unit="mm/day",
        note="Modelled runoff volume ÷ the model's catchment area (mm), as on the Runoff page",
    )


def _flow(reg: Registry) -> Target:
    base = target_from_catalogue("9_1_hydrological_station_master", "daily_mean_flow")
    return replace(
        base,
        label="River discharge",
        unit="m³/s",
        scale=1 / 86400,
        expr_id="m3s",
        native_unit="m³/s",
        note="Daily mean flow (m³/day) ÷ 86 400 s, summed over the scope's districts",
    )


async def _modelled_flow(state: AppState, scope: ScopeSel) -> tuple[pd.Series, str]:
    """The Runoff page's flow: Σ over the scope's districts of the monthly mean runoff depth (mm/day) × district area
    (km²) × 1000 / 86 400 → m³/s (every district needed; a WMA takes each district's share of area)."""
    areas = state.spatial.district_areas()
    if areas is None:
        raise DataUnavailable("Modelled flows need district areas (boundaries)", missing=["district boundaries"])
    newest = await state.meta.newest_date()
    depth = replace(_runoff_depth(state.registry), aggregate="mean", extensive=True)
    per, _ = await district_frame(state, depth, scope, "monthly", newest)
    factors = {k: areas[k] * 1000 / 86400 * f for k, f in sum_fractions(scope, state.spatial).items() if k in areas}
    how = "monthly mean runoff depth × district area (m³/s), summed over the scope's districts (every district needed)"
    return _combine(per, factors, True), how


async def _runoff_anomaly(state: AppState, scope: ScopeSel) -> tuple[pd.Series, str]:
    """The Runoff page's anomaly series: the scope's area-weighted monthly runoff depth vs the area-weighted 7.2
    same-month normal (one district: the stored `Runoff_Anomaly_percent`). Averaging the districts' stored
    percentages instead gave a different history from the page chart (Aug 2026: 42.8 % vs 11.6 %; audit step 1)."""
    from app.modules.runoff.service import RunoffService

    ctx = context(state, scope, await state.meta.newest_date())
    an = (await RunoffService(ctx).anomaly_series()).dropna()
    how = (
        "anomaly of the scope's area-weighted monthly runoff depth vs the 7.2 same-month normal (one district: the "
        "stored Runoff_Anomaly_percent), as on the Runoff page"
    )
    return an, how


def _runoff_anomaly_target(reg: Registry) -> Target:
    base = target_from_catalogue("7.2_derived_runoff_indicators_master", "Runoff_Anomaly_percent")
    return replace(
        base,
        label="Runoff anomaly",
        expr_id="page-anomaly",
        loader=_runoff_anomaly,
        sources=("runoff_variables_master", "7.2_derived_runoff_indicators_master"),
        note="Area-weighted runoff depth vs the 7.2 same-month normal, as the Runoff page's anomaly chart",
    )


def _modelled_flow_target(reg: Registry) -> Target:
    return replace(
        _runoff_depth(reg),
        label="Modelled river flow",
        unit="m³/s",
        aggregate="mean",
        extensive=True,
        expr_id="modelled-flow",
        native_unit="m³/s",
        loader=_modelled_flow,
        note="Modelled runoff depth × district area, as the Runoff page derives flows (not gauged discharge)",
    )


WQ_TABLES = (
    "12.1_physical_water_quality_master",
    "12.2_chemical_water_quality_master",
    "12.3_biological_water_quality_master",
)


async def _wqi_series(state: AppState, scope: ScopeSel) -> tuple[pd.Series, str]:
    """The Water Quality page's index per month: area-weighted mean of the districts' CCME WQI (each over the 12
    months of guideline parameters ending that month)."""
    from app.modules.water_quality.service import WaterQualityService

    ctx = context(state, scope, await state.meta.newest_date())
    df = await WaterQualityService(ctx).wqi_frame()
    per = df.pivot(index="date", columns="district", values="value") if not df.empty else pd.DataFrame()
    how = "area-weighted mean of the districts' CCME WQI (12 months of guideline parameters ending each month)"
    return _combine(per, ctx.weights(), False), how


def _wqi_target(reg: Registry) -> Target:
    return Target(
        table=WQ_TABLES[1],
        column="",
        label="Water Quality Index (CCME)",
        unit="WQI",
        resolution="monthly",
        aggregate="mean",
        extensive=False,
        expr_id="ccme-wqi",
        loader=_wqi_series,
        sources=WQ_TABLES,
        note="CCME WQI (2001) of the guideline parameters over the trailing 12 months, as on the Water Quality page",
    )


S62 = "6.2_rainfall_derived_drought_indices_master"
VARS: dict[str, PageVar] = {
    v.id: v
    for v in (
        PageVar(
            "spi-3",
            "SPI-3",
            "SPI-3",
            _col(S62, "SPI_3_Short_Term_Agricultural_Drought", label="SPI-3"),
            pages=("drought", "rainfall", "ai", "overview"),
        ),
        PageVar(
            "spi-6",
            "SPI-6",
            "SPI-6",
            _col(S62, "SPI_6_Medium_Term_Hydrological_Impacts", label="SPI-6"),
            pages=("drought",),
        ),
        PageVar(
            "spei-3",
            "SPEI-3",
            "SPEI-3",
            _col("meteorological_drought_master", "spei_3", label="SPEI-3"),
            pages=("drought",),
        ),
        PageVar("drought-area", "Area in drought (D1–D4)", "Area in D1–D4 (%)", _d1_area, 1, pages=("drought",)),
        PageVar(
            "rainfall",
            "Monthly rainfall",
            "Monthly rainfall (mm)",
            _col(
                "6.1_core_rainfall_variables_master",
                "Monthly_Total_Precipitation_mm",
                label="Monthly rainfall",
                unit="mm",
            ),
            1,
            pages=("rainfall",),
        ),
        PageVar(
            "spi-1",
            "SPI-1",
            "SPI-1",
            _col(S62, "SPI_1_Meteorological_Drought_Onset", label="SPI-1"),
            pages=("rainfall",),
        ),
        PageVar(
            "temp-anomaly",
            "Temperature anomaly",
            "Tavg anomaly (°C)",
            _col("4_1_temperature_change_master", "Temperature_Anomaly_C", label="Temperature anomaly"),
            pages=("climate",),
        ),
        PageVar("hot-days", "Hot days (Tmax > 35 °C)", "Hot days per month", _hot_days, 1, pages=("climate",)),
        PageVar(
            "ndvi", "NDVI", "NDVI", _col("satellite_remote_sensing", "ndvi", label="NDVI"), 3, pages=("vegetation",)
        ),
        PageVar(
            "vci", "VCI", "VCI (%)", _col("satellite_remote_sensing", "vci", label="VCI"), 1, pages=("vegetation",)
        ),
        PageVar("runoff", "Monthly runoff", "Monthly runoff (mm)", _runoff_depth, 2, pages=("runoff", "surface-water")),
        PageVar(
            "runoff-anomaly",
            "Runoff anomaly",
            "Runoff anomaly (%)",
            _runoff_anomaly_target,
            1,
            pages=("runoff",),
        ),
        PageVar(
            "runoff-flow",
            "Modelled river flow",
            "Monthly mean flow (m³/s)",
            _modelled_flow_target,
            1,
            pages=("runoff",),
        ),
        PageVar("flow", "River discharge", "Monthly mean flow (m³/s)", _flow, 1, pages=("surface-water", "ai")),
        PageVar(
            "gw-depth",
            "Depth to groundwater",
            "Depth to water (m bgl)",
            _col(
                "10_1_borehole_and_aquifer_master",
                "groundwater_table_depth_m_bgl",
                label="Depth to groundwater",
                interpolate=2,
            ),
            2,
            pages=("groundwater", "ai"),
        ),
        PageVar("wqi", "Water Quality Index", "WQI (0–100)", _wqi_target, 1, pages=("water-quality", "ai")),
        PageVar(
            "nitrate",
            "Nitrate (NO₃⁻-N)",
            "Nitrate (mg/L)",
            _col(WQ_TABLES[1], "Nitrate (NO3-N)", label="Nitrate (NO₃⁻-N)"),
            3,
            pages=("water-quality",),
        ),
        PageVar(
            "phosphate",
            "Orthophosphate (PO₄-P)",
            "Orthophosphate (mg/L)",
            _col(WQ_TABLES[1], "Orthophosphate (PO43-)", label="Orthophosphate (PO₄-P)"),
            3,
            pages=("water-quality",),
        ),
        PageVar(
            "turbidity",
            "Turbidity",
            "Turbidity (NTU)",
            _col(WQ_TABLES[0], "Turbidity", label="Turbidity"),
            2,
            pages=("water-quality",),
        ),
        PageVar(
            "ecoli",
            "E. coli",
            "E. coli (cfu/100 mL)",
            _col(WQ_TABLES[2], "E. coli", label="E. coli"),
            0,
            pages=("water-quality",),
        ),
    )
}
# computed for every district in the round (the district outlook maps and per-district probabilities need them)
OUTLOOK_VARS = ("spi-3", "spi-6", "rainfall", "ndvi", "vci", "wqi")


def scope_id(scope: ScopeSel) -> str:
    return "national" if scope.level == "national" else f"{scope.level}={scope.id}"


@dataclass
class Entry:
    status: str  # running | done | failed
    result: dict[str, Any] | None = None
    error: str | None = None
    generated: float = field(default_factory=time.time)
    run_id: str | None = None
    studio: str | None = None
    on_demand: bool = False  # made for a tab that was opened (quick models); dropped when the data change
    retry: bool = False  # failed for a transient reason (a database timeout): recomputed when the tab is opened again


class PageForecasts:
    """The precomputed page forecasts, in memory; recomputed when the newest database date changes."""

    def __init__(self, state: AppState, jobs: ForecastJobs) -> None:
        self.state, self.jobs = state, jobs
        self.entries: dict[tuple[str, str], Entry] = {}
        self.newest: dt.date | None = None
        self.round_started: float | None = None
        self.model = "auto"  # tests swap in a fast baseline
        self.fast = FAST  # districts and on-demand runs
        self.outlook_vars: tuple[str, ...] = OUTLOOK_VARS  # computed per district in the round
        self.only: set[str] | None = None  # tests: compute these variables only
        self.tasks: set[asyncio.Task[Any]] = set()  # on-demand runs in flight (a task needs a strong reference)
        self.active = state.settings.forecast_pages  # the rounds run (else every tab is served on demand)

    def scopes(self) -> list[ScopeSel]:
        """Scopes of the full round (every variable, Auto): South Africa and the provinces."""
        out = [parse_scope()]
        if self.state.settings.forecast_page_provinces:
            out += [parse_scope(province=p) for p in PROVINCES]
        return out

    def district_scopes(self) -> list[ScopeSel]:
        """Scopes of the outlook round (OUTLOOK_VARS, quick models): every district."""
        if not self.state.settings.forecast_page_districts:
            return []
        return [district_scope(d.code) for d in districts.districts()]

    def plan(self) -> list[tuple[PageVar, ScopeSel, str]]:
        full = [(v, s, self.model) for s in self.scopes() for v in VARS.values()]
        outlook = [(VARS[vid], s, self.fast) for s in self.district_scopes() for vid in self.outlook_vars]
        return [(v, s, m) for v, s, m in [*full, *outlook] if self.only is None or v.id in self.only]

    async def refresh(self) -> int:
        """Recompute every page forecast if the data changed since the last round (national scopes first). Entries
        made on demand for the old data are dropped; they are recomputed when their tab is opened again."""
        newest = await self.state.meta.newest_date()
        if newest is not None and newest == self.newest and self.entries:
            return 0
        self.newest, self.round_started = newest, time.time()
        for key in [k for k, e in self.entries.items() if e.on_demand]:
            del self.entries[key]
        # each run is awaited as soon as it is submitted, so an entry turns "done" the moment its run finishes (the
        # preparation of the remaining variables and scopes goes on meanwhile)
        tasks: list[asyncio.Task[None]] = []
        for v, scope, model in self.plan():
            task = await self.start(v, scope, model, background=True)
            if task is not None:
                tasks.append(task)
        await asyncio.gather(*tasks)
        log.info(
            "page forecasts computed", extra={"runs": len(tasks), "seconds": round(time.time() - self.round_started)}
        )
        return len(tasks)

    async def start(
        self, v: PageVar, scope: ScopeSel, model: str, *, background: bool, on_demand: bool = False
    ) -> asyncio.Task[None] | None:
        """Prepare and submit one run; the returned task fills the entry when the run finishes. A series the data
        cannot support (422) becomes a failed entry with the reason, without a run."""
        from app.forecasting.service import prepare

        key = (v.id, scope_id(scope))
        try:
            target = v.make(self.state.registry)
            request, payload, ctx = await prepare(
                self.state, target, scope, model=model, horizon=PAGE_HORIZON, freq="monthly"
            )
        except Exception as exc:  # e.g. 422: too many gaps — kept as the page's reason
            reason = getattr(exc, "error", None) or str(exc)
            retry = False
            if not isinstance(exc, SeriesError) and not hasattr(exc, "error"):  # e.g. a database timeout
                log.exception("page forecast could not be prepared", extra={"var": v.id, "scope": key[1]})
                reason, retry = "The forecast could not be prepared (it is retried when the tab is opened)", True
            self.entries[key] = Entry("failed", error=reason, on_demand=on_demand, retry=retry)
            return None
        try:
            rec, _ = self.jobs.submit(request, payload, ctx, background=background)
        except Busy as exc:  # the Studio queue is full: the tab says so and the next request tries again
            self.entries.pop(key, None)
            log.info("on-demand page forecast deferred", extra={"var": v.id, "scope": key[1], "why": exc.error})
            return None
        prev = self.entries.get(key)
        if prev is None or prev.status != "done":
            self.entries[key] = Entry("running", run_id=rec.id, studio=studio_link(request), on_demand=on_demand)

        async def finish() -> None:
            await self.jobs.wait(rec)
            if rec.status == "done" and rec.result is not None:
                self.entries[key] = Entry(
                    "done",
                    {**rec.context, **rec.result},
                    run_id=rec.id,
                    studio=studio_link(rec.request),
                    generated=rec.finished or time.time(),
                    on_demand=on_demand,
                )
            elif key not in self.entries or self.entries[key].status != "done":
                self.entries[key] = Entry(
                    "failed", error=(rec.error or {}).get("detail", "The forecast failed"), on_demand=on_demand
                )

        return asyncio.create_task(finish())

    async def on_demand(self, v: PageVar, scope: ScopeSel) -> None:
        """Compute one (variable, area) pair with the quick models because its tab was opened."""
        task = await self.start(v, scope, self.fast, background=False, on_demand=True)
        if task is not None:
            await task

    async def keep_fresh(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                log.exception("page forecast round failed")
            await asyncio.sleep(self.state.settings.forecast_page_check_s)

    # ---------------------------------------------------------------- lookup

    def planned(self, var_id: str, scope: ScopeSel) -> bool:
        """Whether the round computes this (variable, scope) pair (nothing is planned when the page forecasts are
        switched off: every tab is then served on demand)."""
        if not self.active:
            return False
        sid = scope_id(scope)
        if sid in {scope_id(s) for s in self.scopes()}:
            return True
        return var_id in self.outlook_vars and sid in {scope_id(s) for s in self.district_scopes()}

    def entry(self, var_id: str, scope: ScopeSel) -> Entry:
        v = VARS[var_id]
        key = (var_id, scope_id(scope))
        e = self.entries.get(key)
        if e is not None and e.status == "failed" and e.retry and self.state.settings.forecast_page_on_demand:
            e = None  # a transient failure: compute it again now (quick models), as for a tab opened the first time
        if e is None and (not self.planned(var_id, scope) or key in self.entries):
            if not self.state.settings.forecast_page_on_demand:
                raise DataUnavailable(
                    f"Page forecasts of {v.label} are computed for South Africa and the nine provinces; run this area "
                    "in the Forecasting Studio (AI & Predictive Analytics page).",
                    missing=[f"forecast:{var_id}:{key[1]}"],
                )
            # first request for this area: compute it now with the quick models; the tab says so meanwhile
            self.entries[key] = Entry("running", on_demand=True)
            task = asyncio.get_running_loop().create_task(self.on_demand(v, scope))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            e = self.entries[key]
        if e is None or e.status == "running":
            what = (
                f"The {v.label} forecast for {scope.name} is being computed (about a minute); reload shortly."
                if e is not None and e.on_demand
                else f"The {v.label} forecast is being computed (page forecasts are recomputed when new data arrive); "
                "try again in a few minutes."
            )
            exc = DataUnavailable(what, missing=[f"forecast:{var_id}"])
            exc.headers = {"Cache-Control": "no-store"}  # a temporary answer: never cached by browsers or proxies
            raise exc
        if e.status == "failed" or e.result is None:
            raise DataUnavailable(f"No {v.label} forecast: {e.error}", missing=[f"forecast:{var_id}"])
        return e

    def done(self, var_id: str, scope: ScopeSel) -> Entry | None:
        """The finished entry, or None (never triggers a run): for products that combine many areas."""
        e = self.entries.get((var_id, scope_id(scope)))
        return e if e is not None and e.status == "done" and e.result is not None else None


def studio_link(request: dict[str, Any]) -> str | None:
    """The Forecasting Studio URL that reruns this forecast; None for derived page variables (runoff depth, hot days,
    area in drought, discharge in m³/s, WQI, modelled flow), which the Studio cannot request."""
    if request.get("expr"):
        return None
    q = {
        "table": request["table"],
        "column": request["column"],
        "model": request["model"],
        "horizon": request["horizon"],
    }
    sc = request.get("scope", {})
    if sc.get("level") and sc["level"] != "national":
        q["level"], q["area"] = sc["level"], sc.get("id", "")
    return "/ai?" + urllib.parse.urlencode(q)


# ---------------------------------------------------------------- contract outputs


def forecast_info(e: Entry, horizon: int) -> c.ForecastInfo:
    r = e.result or {}
    m, met = r["model"], r.get("metrics", {})
    skill = met.get("skill")
    beats = None if skill is None else skill > 0
    return c.ForecastInfo(
        label="Statistical forecast",
        model=m["id"],
        model_label=m["label"],
        why=m.get("why"),
        skill=rnd(skill, 2),
        mase=rnd(met.get("mase"), 2),
        coverage=rnd(met.get("coverage"), 0),
        beats_climatology=beats,
        skill_note=None
        if beats in (True, None)
        else "This model does not beat the long-term average for this variable.",
        data_as_of=r["asOf"],
        forecast_start=r["forecastStart"],
        horizon=horizon,
        generated_at=dt.datetime.fromtimestamp(e.generated, dt.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        disclaimer=r.get(
            "disclaimer", "Statistical forecast from historical data; not an official SAWS / DWS outlook."
        ),
        studio=e.studio,
    )


def _note(e: Entry, horizon: int) -> str:
    r = e.result or {}
    info = forecast_info(e, horizon)
    skill = "n/a" if info.skill is None else f"{info.skill:.2f}"
    data = dt.date.fromisoformat(r["asOf"]).strftime("%b %Y")
    tail = (
        " This model does not beat the long-term average for this variable." if info.beats_climatology is False else ""
    )
    series_note = (r.get("target") or {}).get("note") or ""
    if "interpolated" in series_note:  # groundwater depth: unsampled months filled before the forecast (D41.3)
        tail += " " + series_note.split(": ", 1)[-1].split("; ", 1)[-1].rstrip(".") + "."
    return (
        f"Statistical forecast ({info.model_label}, skill vs climatology {skill}, data to {data}). "
        f"{info.disclaimer}{tail}"
    )


def horizon_param(ctx: Ctx, default: int = 12) -> int:
    raw = ctx.params.get("horizon")
    if raw in (None, ""):
        return default
    if raw not in tuple(str(h) for h in HORIZONS):
        from app.core.errors import BadRequest

        raise BadRequest(
            f"Unknown horizon {raw!r}", issues=[{"param": "horizon", "allowed": [str(h) for h in HORIZONS]}]
        )
    return int(raw)


def pages(state: AppState) -> PageForecasts:
    pf: PageForecasts | None = getattr(state, "page_forecasts", None)
    if pf is None:
        raise DataUnavailable("Forecasts are not enabled on this server", missing=["forecast"])
    return pf


def _hist(r: dict[str, Any]) -> list[list[Any]]:
    return [p for p in r["history"] if p[1] is not None]


def series_set(ctx: Ctx, var_id: str, *, set_id: str, title: str | None = None) -> dict[str, Any]:
    """Observed (last 36 months) + forecast median + 10–90 % band, the divider (`asOf`) at the last observed month."""
    v = VARS[var_id]
    e = pages(ctx.state).entry(var_id, ctx.for_entity().filters.scope)
    r = e.result or {}
    h = horizon_param(ctx)
    hist = _hist(r)[-HISTORY_SHOWN:]
    fc = r["forecast"][:h]
    last = hist[-1] if hist else None
    start = [[last[0], last[1], last[1]]] if last else []
    band = start + [[d, rnd(lo, v.decimals), rnd(hi, v.decimals)] for d, _, lo, hi in fc]
    med = ([[last[0], last[1]]] if last else []) + [[d, rnd(m, v.decimals)] for d, m, _, _ in fc]
    unit = (r.get("target") or {}).get("unit") or ""
    body = c.SeriesSet(
        id=set_id,
        title=title or f"{v.label} forecast",
        x_type="time",
        axes=[c.Axis(unit=unit or "—", label=v.axis)],
        series=[
            c.Series(key="band", name="Uncertainty (10th–90th pct)", kind="band", color=BAND, data=band),
            c.Series(
                key="obs",
                name="Observed",
                kind="line",
                color=OBS,
                symbol=True,
                data=[[d, rnd(x, v.decimals)] for d, x in hist],
            ),
            c.Series(key="median", name="Forecast (median)", kind="line", color=MEDIAN, dash=True, data=med),
        ],
        as_of=r["asOf"],
        note=_note(e, h),
        source=r.get("sources"),
        aggregation=ctx.aggregation,
        is_stale=ctx.stale(dt.date.fromisoformat(r["asOf"]), "monthly"),
        period_end=iso(_month_end(dt.date.fromisoformat(r["asOf"]))),
        forecast=forecast_info(e, h),
    )
    return body.dump()


def paired_series(ctx: Ctx, var_ids: list[str], *, set_id: str, title: str, axes: list[c.Axis]) -> dict[str, Any]:
    """Several variables on one chart: an observed and a dashed forecast (median) series per variable, no bands
    (the Water Quality page's parameter forecast). `axes[i]` is chosen per variable by its unit."""
    pf = pages(ctx.state)
    scope = ctx.for_entity().filters.scope
    series: list[c.Series] = []
    infos: list[c.ForecastInfo] = []
    sources: set[str] = set()
    as_of: list[str] = []
    h = horizon_param(ctx)
    for i, vid in enumerate(var_ids):
        v = VARS[vid]
        try:
            e = pf.entry(vid, scope)
        except DataUnavailable as exc:
            ctx.explain(f"series.{vid}", exc.error)
            series.append(c.Series(key=vid, name=v.label, kind="line", color=SERIES[i % len(SERIES)], data=[]))
            series.append(c.Series(key=f"{vid}-fc", name=f"{v.label} forecast", kind="line", dash=True, data=[]))
            continue
        r = e.result or {}
        unit = (r.get("target") or {}).get("unit") or ""
        axis = next((k for k, a in enumerate(axes) if a.unit == unit), 0)
        hist = _hist(r)[-HISTORY_SHOWN:]
        last = hist[-1] if hist else None
        med = ([[last[0], last[1]]] if last else []) + [[d, rnd(m, v.decimals)] for d, m, _, _ in r["forecast"][:h]]
        colour = SERIES[i % len(SERIES)]
        series.append(
            c.Series(
                key=vid,
                name=f"{v.label} ({unit})",
                kind="line",
                color=colour,
                axis=axis,
                data=[[d, rnd(x, v.decimals)] for d, x in hist],
            )
        )
        series.append(
            c.Series(
                key=f"{vid}-fc", name=f"{v.label} forecast", kind="line", color=colour, dash=True, axis=axis, data=med
            )
        )
        infos.append(forecast_info(e, h))
        sources |= set(r.get("sources", []))
        as_of.append(r["asOf"])
    if not infos:
        raise DataUnavailable(next(iter(ctx.reasons.values()), "No forecasts"), missing=[set_id])
    oldest = min(as_of)
    body = c.SeriesSet(
        id=set_id,
        title=title,
        x_type="time",
        axes=axes,
        series=series,
        as_of=oldest,
        note="Statistical forecasts (median; the band of each parameter is in its own forecast). "
        + infos[0].disclaimer,
        source=sorted(sources),
        aggregation=ctx.aggregation,
        is_stale=ctx.stale(dt.date.fromisoformat(oldest), "monthly"),
        period_end=iso(_month_end(dt.date.fromisoformat(oldest))),
        forecast=_combined(infos),
    )
    return body.dump()


def _month_end(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


def _at(r: dict[str, Any], months: int) -> tuple[float | None, float | None, float | None]:
    fc = r["forecast"]
    if months > len(fc):
        return None, None, None
    _, m, lo, hi = fc[months - 1]
    return m, lo, hi


def _sd(lo: float, hi: float) -> float:
    """σ of the normal whose 10–90 % band is [lo, hi] (as the engine's class probabilities assume)."""
    return max((hi - lo) / (2 * Z90), 1e-6)


def p_above(threshold: float, m: float, lo: float, hi: float) -> float:
    """Probability (0–1) that the value exceeds `threshold`, from the forecast's median and band."""
    from scipy.stats import norm

    return float(1.0 - norm.cdf(threshold, loc=m, scale=_sd(lo, hi)))


def _lead_date(r: dict[str, Any], months: int) -> dt.date:
    return dt.date.fromisoformat(r["forecast"][months - 1][0])


def indicators_table(ctx: Ctx, var_ids: list[str], *, table_id: str, title: str) -> dict[str, Any]:
    """One row per variable: current (last observed month) and the median 3 / 6 / 12 / 24 months ahead, with the
    model and its skill. Variables without a forecast keep their row, null, with the reason."""
    rows: list[dict[str, Any]] = []
    unavailable: list[c.UnavailableField] = []
    infos: list[c.ForecastInfo] = []
    sources: set[str] = set()
    as_of: list[str] = []
    pf = pages(ctx.state)
    for vid in var_ids:
        v = VARS[vid]
        try:
            e = pf.entry(vid, ctx.for_entity().filters.scope)
        except DataUnavailable as exc:
            rows.append(
                {
                    "id": vid,
                    "indicator": v.label,
                    "unit": None,
                    "current": None,
                    "model": None,
                    "skill": None,
                    **{f"h{h}": None for h in HORIZONS},
                }
            )
            unavailable.append(c.UnavailableField(field=f"rows.{vid}", reason=exc.error))
            continue
        r = e.result or {}
        info = forecast_info(e, PAGE_HORIZON)
        infos.append(info)
        sources |= set(r.get("sources", []))
        as_of.append(r["asOf"])
        hist = _hist(r)
        row: dict[str, Any] = {
            "id": vid,
            "indicator": v.label,
            "unit": (r.get("target") or {}).get("unit") or "—",
            "current": rnd(hist[-1][1], v.decimals) if hist else None,
            "model": info.model_label,
            "skill": info.skill,
        }
        for h in HORIZONS:
            row[f"h{h}"] = rnd(_at(r, h)[0], v.decimals)
        rows.append(row)
    if not infos:
        raise DataUnavailable(unavailable[0].reason if unavailable else "No forecasts", missing=[table_id])
    cols = [
        c.Column(key="indicator", label="Indicator", type="text"),
        c.Column(key="unit", label="Unit", type="text"),
        c.Column(key="current", label="Current", type="number", decimals=2),
        *[c.Column(key=f"h{h}", label=f"{h} months", type="number", decimals=2) for h in HORIZONS],
        c.Column(key="model", label="Model", type="text"),
        c.Column(key="skill", label="Skill vs climatology", type="number", decimals=2, good_direction="up"),
    ]
    oldest = min(as_of)
    body = c.TableSet(
        id=table_id,
        title=title,
        columns=cols,
        rows=rows,
        row_key="id",
        as_of=oldest,
        source=sorted(sources),
        aggregation=ctx.aggregation,
        forecast=_combined(infos),
        **({"unavailable": unavailable} if unavailable else {}),
    )
    return body.dump()


def _combined(infos: list[c.ForecastInfo]) -> c.ForecastInfo:
    """One `forecast` block for a table of several forecasts: the oldest data date, the weakest skill."""
    worst = min(infos, key=lambda i: i.skill if i.skill is not None else 9e9)
    first = min(infos, key=lambda i: i.data_as_of)
    models = sorted({i.model_label for i in infos})
    return worst.model_copy(
        update={
            "model": "auto",
            "model_label": " / ".join(models),
            "why": "Auto chose the model of each row by backtest MASE.",
            "data_as_of": first.data_as_of,
            "forecast_start": first.forecast_start,
            "studio": None,
        }
    )


LEADS = (1, 2, 3, 4, 5, 6)  # class probabilities: months ahead
RISK_HORIZONS = (3, 6, 12, 24)  # province × horizon matrix


def class_probability(ctx: Ctx, var_id: str = "spi-3") -> dict[str, Any]:
    """Probability (%) of each SPI class at 1, 3, 6 and 12 months ahead (stacked)."""
    e = pages(ctx.state).entry(var_id, ctx.for_entity().filters.scope)
    r = e.result or {}
    classes = r.get("classes") or []
    if not classes:
        raise DataUnavailable("Class probabilities need an SPI / SPEI forecast", missing=["class-probability"])
    cats = [f"{h} month" + ("s" if h > 1 else "") for h in LEADS]
    series = [
        c.CategorySeries(
            key=k,
            name="No drought" if k == "None" else drought_label(k),
            values=[rnd(classes[h - 1]["probabilities"].get(k), 1) for h in LEADS],
            color=CLASS_COLORS[k],
            stack="p",
        )
        for k in D_CLASSES
    ]
    body = c.CategorySet(
        id="class-probability",
        title=f"{VARS[var_id].label} class probability by lead time",
        unit="%",
        categories=cats,
        series=series,
        as_of=r["asOf"],
        source=r.get("sources"),
        aggregation=ctx.aggregation,
        forecast=forecast_info(e, max(LEADS)),
    )
    return body.dump()


def province_risk(ctx: Ctx, var_id: str = "spi-3") -> dict[str, Any]:
    """Probability (0–1) of D2 or worse per province 3, 6, 12 and 24 months ahead (from each province's forecast)."""
    pf = pages(ctx.state)
    rows: list[str] = []
    values: list[list[float | None]] = []
    infos: list[c.ForecastInfo] = []
    sources: set[str] = set()
    for p in PROVINCES:
        scope = parse_scope(province=p)
        name = districts.PROVINCES.get(p, scope.name)
        e = pf.done(var_id, scope)
        rows.append(name)
        if e is None:
            values.append([None for _ in RISK_HORIZONS])
            continue
        r = e.result or {}
        cls = r.get("classes") or []
        values.append(
            [
                rnd(sum(cls[h - 1]["probabilities"].get(k, 0.0) for k in ("D2", "D3", "D4")) / 100, 3)
                if len(cls) >= h
                else None
                for h in RISK_HORIZONS
            ]
        )
        infos.append(forecast_info(e, max(RISK_HORIZONS)))
        sources |= set(r.get("sources", []))
    if not infos:
        raise DataUnavailable(
            "Province forecasts are still being computed; try again in a few minutes.", missing=["province-risk"]
        )
    body = c.MatrixSet(
        id="province-risk",
        title=f"Probability of severe drought (D2+, {VARS[var_id].label})",
        rows=rows,
        cols=[f"{h} months" for h in RISK_HORIZONS],
        values=values,
        unit="probability",
        scale="index01",
        as_of=min(i.data_as_of for i in infos),
        source=sorted(sources),
        forecast=_combined(infos),
    )
    return body.dump()


# ---------------------------------------------------------------- products from the district / province forecasts


def _same_month_mean(r: dict[str, Any], month: int) -> float | None:
    vals = [x for d, x in _hist(r) if dt.date.fromisoformat(d).month == month]
    return sum(vals) / len(vals) if vals else None


def outlook_map(
    ctx: Ctx,
    var_id: str,
    lead: int,
    *,
    layer: str,
    title: str,
    scale: str,
    unit: str,
    decimals: int,
    kind: str = "value",
) -> dict[str, Any]:
    """Choropleth of each district's forecast `lead` months ahead: the median (`value`), its departure from the
    district's same-month mean (`anomaly`) or that departure in % of the mean (`pct-normal`). Districts whose
    forecast is not computed are null, with the reason; the title carries the target month."""
    pf = pages(ctx.state)
    w = ctx.weights()
    values: dict[str, float | None] = {}
    infos: list[c.ForecastInfo] = []
    sources: set[str] = set()
    missing: list[str] = []
    target: dt.date | None = None
    for code in sorted(w):
        e = pf.done(var_id, district_scope(code))
        if e is None:
            values[code] = None
            missing.append(code)
            continue
        r = e.result or {}
        m, _lo, _hi = _at(r, lead)
        when = _lead_date(r, lead)
        target = when if target is None else max(target, when)
        out: float | None = m
        if m is not None and kind in ("anomaly", "pct-normal"):
            normal = _same_month_mean(r, when.month)
            if normal is None:
                out = None
            elif kind == "anomaly":
                out = m - normal
            else:
                out = (m - normal) / normal * 100 if normal else None
        values[code] = rnd(out, decimals)
        infos.append(forecast_info(e, lead))
        sources |= set(r.get("sources", []))
    if not infos:
        raise DataUnavailable(
            f"The district {VARS[var_id].label} forecasts are being computed; try again in a few minutes.",
            missing=[f"map:{layer}"],
        )
    if missing:
        ctx.explain(
            "values",
            f"{len(missing)} district(s) without a {VARS[var_id].label} forecast yet (being computed, or the series "
            "cannot support one)",
        )
    assert target is not None
    info = _combined(infos)
    as_of = info.data_as_of
    body = c.MapLayer(
        id=layer,
        title=f"{title} – {target:%b %Y}",
        kind="choropleth",
        level="municipality",
        scale=scale,
        unit=unit,
        date=iso(target) or "",
        values=values,
        geometry="districts",
        as_of=as_of,
        source=sorted(sources),
        is_stale=ctx.stale(dt.date.fromisoformat(as_of), "monthly"),
        period_end=iso(_month_end(dt.date.fromisoformat(as_of))),
        aggregation="district",
        forecast=info,
    )
    return body.dump()


def exceedance_series(
    ctx: Ctx, var_id: str, *, set_id: str, title: str, quantile: float = 0.9, name: str
) -> dict[str, Any]:
    """Probability (%) per forecast month that the variable exceeds its historical `quantile` (Q10 = the flow
    exceeded 10 % of the time = the 90th percentile of the monthly history), from the median and the 10–90 % band."""
    v = VARS[var_id]
    e = pages(ctx.state).entry(var_id, ctx.for_entity().filters.scope)
    r = e.result or {}
    h = horizon_param(ctx)
    hist = _hist(r)
    threshold = float(pd.Series([x for _, x in hist], dtype=float).quantile(quantile))
    data = [[d, rnd(p_above(threshold, m, lo, hi) * 100, 1)] for d, m, lo, hi in r["forecast"][:h]]
    unit = (r.get("target") or {}).get("unit") or ""
    q = f"Q{round((1 - quantile) * 100)}"
    body = c.SeriesSet(
        id=set_id,
        title=title,
        x_type="time",
        axes=[c.Axis(unit="%", label=f"Probability of exceeding {q}", min=0, max=100)],
        series=[c.Series(key="p", name=name, kind="line", color=SERIES[3], symbol=True, data=data)],
        ref_lines=[c.RefLine(y=50, label="Even odds")],
        as_of=r["asOf"],
        note=f"{q} = {rnd(threshold, v.decimals)} {unit} ({v.label} exceeded {round((1 - quantile) * 100)} % of the "
        f"months {dt.date.fromisoformat(hist[0][0]):%b %Y} – {dt.date.fromisoformat(hist[-1][0]):%b %Y}); the "
        "probability assumes a normal distribution centred on the forecast median with the 10–90 % band as its "
        "spread. " + _note(e, h),
        source=r.get("sources"),
        aggregation=ctx.aggregation,
        is_stale=ctx.stale(dt.date.fromisoformat(r["asOf"]), "monthly"),
        period_end=iso(_month_end(dt.date.fromisoformat(r["asOf"]))),
        forecast=forecast_info(e, h),
    )
    return body.dump()


def _p_no_drought(cls: dict[str, Any]) -> float:
    return 100.0 - float(cls["pDrought"])


def recovery_table(ctx: Ctx, var_id: str = "spi-3") -> dict[str, Any]:
    """Expected conditions 3 / 6 / 12 / 24 months ahead (the class of the median SPI-3) and the probability of no
    drought (SPI-3 above the D1 threshold) at each."""
    e = pages(ctx.state).entry(var_id, ctx.for_entity().filters.scope)
    r = e.result or {}
    classes = r.get("classes") or []
    if len(classes) < max(HORIZONS):
        raise DataUnavailable("Recovery outlooks need the SPI-3 class probabilities", missing=["recovery"])
    rows = []
    for h in HORIZONS:
        cls = classes[h - 1]
        rows.append(
            {
                "id": f"h{h}",
                "timeFrame": f"{h} months ({dt.date.fromisoformat(cls['date']):%b %Y})",
                "conditions": "No drought" if cls["class"] == "None" else drought_label(cls["class"]),
                "conditionClass": cls["class"],
                "probability": rnd(_p_no_drought(cls), 1),
                "pDrought": rnd(cls["pDrought"], 1),
            }
        )
    body = c.TableSet(
        id="recovery",
        title=f"Drought recovery outlook ({VARS[var_id].label})",
        columns=[
            c.Column(key="timeFrame", label="Time frame", type="text"),
            c.Column(key="conditions", label="Expected conditions", type="class", scale="drought"),
            c.Column(key="probability", label="Recovery probability", type="number", unit="%", decimals=0),
        ],
        rows=rows,
        row_key="id",
        as_of=r["asOf"],
        source=r.get("sources"),
        aggregation=ctx.aggregation,
        forecast=forecast_info(e, max(HORIZONS)),
    )
    return body.dump()


def recovery_province_series(ctx: Ctx, var_id: str = "spi-3") -> dict[str, Any]:
    """Probability of no drought (%) per forecast month for each province, from the provinces' SPI-3 forecasts."""
    pf = pages(ctx.state)
    h = horizon_param(ctx)
    series: list[c.Series] = []
    infos: list[c.ForecastInfo] = []
    sources: set[str] = set()
    for i, p in enumerate(PROVINCES):
        scope = parse_scope(province=p)
        e = pf.done(var_id, scope)
        if e is None:
            ctx.explain(f"series.{p}", f"The {scope.name} forecast is being computed")
            series.append(c.Series(key=p, name=scope.name, kind="line", color=SERIES[i % len(SERIES)], data=[]))
            continue
        r = e.result or {}
        cls = (r.get("classes") or [])[:h]
        series.append(
            c.Series(
                key=p,
                name=scope.name,
                kind="line",
                color=SERIES[i % len(SERIES)],
                data=[[x["date"], rnd(_p_no_drought(x), 1)] for x in cls],
            )
        )
        infos.append(forecast_info(e, h))
        sources |= set(r.get("sources", []))
    if not infos:
        raise DataUnavailable(
            "Province forecasts are still being computed; try again in a few minutes.", missing=["recovery-province"]
        )
    info = _combined(infos)
    body = c.SeriesSet(
        id="recovery-province",
        title="Probability of no drought by province (SPI-3 forecast)",
        x_type="time",
        axes=[c.Axis(unit="%", label="Recovery probability", min=0, max=100)],
        series=series,
        as_of=info.data_as_of,
        note="Probability that the province's SPI-3 is above the D1 threshold in each month, from its statistical "
        "forecast (normal distribution centred on the median, 10–90 % band as spread). " + info.disclaimer,
        source=sorted(sources),
        aggregation="district-area-weighted",
        forecast=info,
    )
    return body.dump()


EARLY_WARNING = ("spi-3", "spi-6", "spei-3", "drought-area")


def early_warning_table(ctx: Ctx, table_id: str = "early-warning") -> dict[str, Any]:
    """Indicator, current status (class of the last observation), forecast 3 months ahead (class of the median) and
    the confidence: the probability of that class, or, for the area in drought, of the forecast direction."""
    pf = pages(ctx.state)
    scope = ctx.for_entity().filters.scope
    rows: list[dict[str, Any]] = []
    unavailable: list[c.UnavailableField] = []
    infos: list[c.ForecastInfo] = []
    sources: set[str] = set()
    for vid in EARLY_WARNING:
        v = VARS[vid]
        try:
            e = pf.entry(vid, scope)
        except DataUnavailable as exc:
            rows.append({"id": vid, "indicator": v.label, "status": None, "forecast": None, "confidence": None})
            unavailable.append(c.UnavailableField(field=f"rows.{vid}", reason=exc.error))
            continue
        r = e.result or {}
        hist = _hist(r)
        cur = float(hist[-1][1])
        m, lo, hi = _at(r, 3)
        if m is None or lo is None or hi is None:
            rows.append({"id": vid, "indicator": v.label, "status": None, "forecast": None, "confidence": None})
            unavailable.append(c.UnavailableField(field=f"rows.{vid}", reason="No 3-month forecast"))
            continue
        row: dict[str, Any]
        if r.get("classes"):
            cls = r["classes"][2]
            fc_cls = cls["class"]
            row = {
                "status": "No drought" if drought_class(cur) == "None" else drought_label(drought_class(cur)),
                "statusClass": drought_class(cur),
                "forecast": "No drought" if fc_cls == "None" else drought_label(fc_cls),
                "forecastClass": fc_cls,
                "confidence": rnd(cls["probabilities"].get(fc_cls), 0),
            }
        else:
            up = m >= cur
            p_dir = p_above(cur, m, lo, hi) if up else 1 - p_above(cur, m, lo, hi)
            row = {
                "status": f"{cur:.0f} % of the area in D1–D4",
                "forecast": f"{m:.0f} % ({'more' if up else 'less'} area in drought)",
                "confidence": rnd(p_dir * 100, 0),
            }
        rows.append({"id": vid, "indicator": v.label, "current": rnd(cur, v.decimals), "h3": rnd(m, v.decimals), **row})
        infos.append(forecast_info(e, 3))
        sources |= set(r.get("sources", []))
    if not infos:
        raise DataUnavailable(unavailable[0].reason if unavailable else "No forecasts", missing=[table_id])
    info = _combined(infos)
    body = c.TableSet(
        id=table_id,
        title="Early-warning signals (3-month statistical outlook)",
        columns=[
            c.Column(key="indicator", label="Indicator", type="text"),
            c.Column(key="status", label="Current status", type="text"),
            c.Column(key="forecast", label="Forecast (3 months)", type="text"),
            c.Column(key="confidence", label="Confidence", type="number", unit="%", decimals=0),
        ],
        rows=rows,
        row_key="id",
        as_of=info.data_as_of,
        source=sorted(sources),
        aggregation=ctx.aggregation,
        forecast=info,
        **({"unavailable": unavailable} if unavailable else {}),
    )
    return body.dump()


def band_probabilities(
    m: float, lo: float, hi: float, classes: list[tuple[str, float | None]], *, descending: bool = True
) -> dict[str, float]:
    """Probability (%) of each class from a normal centred on `m` with the band as spread. `classes` are (id, lower
    bound) from the best class down (`descending`: the first class is `≥ bound`, the last has no bound)."""
    from scipy.stats import norm

    sd = _sd(lo, hi)
    out: dict[str, float] = {}
    prev = 0.0  # P(value ≥ the previous class's bound): nothing lies above the best class
    for cid, bound in classes:
        if bound is None:
            out[cid] = max(0.0, 1.0 - prev) * 100
            break
        tail = float(1.0 - norm.cdf(bound, loc=m, scale=sd))  # P(value ≥ bound)
        out[cid] = max(0.0, tail - prev) * 100
        prev = tail
    return out


def wqi_bands() -> list[tuple[str, float | None]]:
    cfg = thresholds()["wqi"]
    return [(str(x["id"]), float(x["min"])) for x in cfg["classes"]] + [(str(cfg["otherwise"]), None)]


def class_outlook(ctx: Ctx, var_id: str, *, set_id: str, title: str, leads: int = 12) -> dict[str, Any]:
    """Probability (%) of each WQI band 1 … `leads` months ahead (stacked), from the WQI forecast."""
    e = pages(ctx.state).entry(var_id, ctx.for_entity().filters.scope)
    r = e.result or {}
    fc = r["forecast"][:leads]
    bands = wqi_bands()
    probs = [band_probabilities(m, lo, hi, bands) for _, m, lo, hi in fc]
    cats = [f"{dt.date.fromisoformat(d):%b %Y}" for d, *_ in fc]
    series = [
        c.CategorySeries(
            key=k.lower().replace(" ", "-"),
            name=k,
            values=[rnd(p.get(k, 0.0), 1) for p in probs],
            color=WQI_COLORS[k],
            stack="p",
        )
        for k in WQI_CLASSES
    ]
    body = c.CategorySet(
        id=set_id,
        title=title,
        unit="%",
        categories=cats,
        series=series,
        as_of=r["asOf"],
        source=r.get("sources"),
        aggregation=ctx.aggregation,
        forecast=forecast_info(e, leads),
    )
    return body.dump()


def recovery_probability(ctx: Ctx, var_id: str = "vci", threshold: float | None = None) -> dict[str, Any]:
    """Probability (%) that VCI is at or above the stress threshold (40 %) 12 months ahead: per province for South
    Africa, per district for a province, WMA or district (from the provinces' / districts' forecasts)."""
    pf = pages(ctx.state)
    scope = ctx.for_entity().filters.scope
    limit = float(thresholds()["vegetation"]["stress_below"]) if threshold is None else threshold
    if scope.level == "national":
        units = [(districts.PROVINCES[p], parse_scope(province=p)) for p in PROVINCES]
    else:
        units = [
            (d.official_name, district_scope(d.code))
            for d in (districts.by_code(x) for x in scope.district_codes)
            if d is not None
        ]
    data: list[list[Any]] = []
    infos: list[c.ForecastInfo] = []
    sources: set[str] = set()
    missing = 0
    for name, sc in units:
        e = pf.done(var_id, sc)
        if e is None:
            data.append([name, None])
            missing += 1
            continue
        r = e.result or {}
        m, lo, hi = _at(r, 12)
        ok = m is not None and lo is not None and hi is not None
        data.append([name, rnd(p_above(limit, m, lo, hi) * 100, 1) if ok else None])  # type: ignore[arg-type]
        infos.append(forecast_info(e, 12))
        sources |= set(r.get("sources", []))
    if not infos:
        raise DataUnavailable(
            f"The {VARS[var_id].label} forecasts of these areas are being computed; try again in a few minutes.",
            missing=["recovery-probability"],
        )
    if missing:
        ctx.explain("series.p", f"{missing} area(s) without a {VARS[var_id].label} forecast yet")
    info = _combined(infos)
    body = c.SeriesSet(
        id="recovery-probability",
        title=f"Probability of no vegetation stress (VCI ≥ {limit:.0f} %) in 12 months",
        x_type="category",
        axes=[c.Axis(unit="%", label="Probability", min=0, max=100)],
        series=[c.Series(key="p", name="Recovery probability (12 months)", kind="bar", color=SERIES[1], data=data)],
        as_of=info.data_as_of,
        note=f"Probability that VCI is at or above {limit:.0f} % (Kogan: below is drought stress) 12 months ahead, "
        "from each area's statistical VCI forecast (normal distribution centred on the median, 10–90 % band as "
        "spread). " + info.disclaimer,
        source=sorted(sources),
        aggregation="district" if scope.level != "national" else "district-area-weighted",
        forecast=info,
    )
    return body.dump()
