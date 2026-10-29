"""Precomputed forecasts for the module pages (DECISIONS D37).

At startup, and again whenever the newest date in the database changes, an **Auto** run (24 months) is made for each
page's key variables, for South Africa and each province (national first). They are served by the contract forecast
endpoints that used to answer 501: forecast series (observed + median + 10–90 % band, the divider at the last
observed month), indicator tables (current value and 3 / 6 / 12 / 24 months ahead), SPI class probabilities by lead
time and the province × lead-time probability of D2+. Each carries `forecast` (model, skill, data date, disclaimer).

Only products that follow directly from a forecast variable are served; district forecasts (maps), settlement /
population counts, recovery probabilities, reservoir storage (withheld, D2) and water-quality indices stay 501.
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

from app.core import districts
from app.core.errors import DataUnavailable
from app.core.filters import ScopeSel, parse_scope
from app.core.palettes import SERIES
from app.core.provenance import iso
from app.core.stats import rnd
from app.core.thresholds import drought_label, thresholds
from app.forecasting.jobs import ForecastJobs
from app.forecasting.series import Target, target_from_catalogue
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
            _col("7.2_derived_runoff_indicators_master", "Runoff_Anomaly_percent", label="Runoff anomaly"),
            1,
            pages=("runoff",),
        ),
        PageVar("flow", "River discharge", "Monthly mean flow (m³/s)", _flow, 1, pages=("surface-water", "ai")),
        PageVar(
            "gw-depth",
            "Depth to groundwater",
            "Depth to water (m bgl)",
            _col("10_1_borehole_and_aquifer_master", "groundwater_table_depth_m_bgl", label="Depth to groundwater"),
            2,
            pages=("groundwater", "ai"),
        ),
    )
}


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


class PageForecasts:
    """The precomputed page forecasts, in memory; recomputed when the newest database date changes."""

    def __init__(self, state: AppState, jobs: ForecastJobs) -> None:
        self.state, self.jobs = state, jobs
        self.entries: dict[tuple[str, str], Entry] = {}
        self.newest: dt.date | None = None
        self.round_started: float | None = None
        self.model = "auto"  # tests swap in a fast baseline
        self.only: set[str] | None = None  # tests: compute these variables only

    def scopes(self) -> list[ScopeSel]:
        out = [parse_scope()]
        if self.state.settings.forecast_page_provinces:
            out += [parse_scope(province=p) for p in PROVINCES]
        return out

    async def refresh(self) -> int:
        """Recompute every page forecast if the data changed since the last round (national scopes first)."""
        from app.forecasting.service import prepare

        newest = await self.state.meta.newest_date()
        if newest is not None and newest == self.newest and self.entries:
            return 0
        self.newest, self.round_started = newest, time.time()

        async def finish(key: tuple[str, str], rec: Any) -> None:
            await self.jobs.wait(rec)
            if rec.status == "done" and rec.result is not None:
                self.entries[key] = Entry(
                    "done",
                    {**rec.context, **rec.result},
                    run_id=rec.id,
                    studio=studio_link(rec.request),
                    generated=rec.finished or time.time(),
                )
            elif key not in self.entries or self.entries[key].status != "done":
                self.entries[key] = Entry("failed", error=(rec.error or {}).get("detail", "The forecast failed"))

        # each run is awaited as soon as it is submitted, so an entry turns "done" the moment its run finishes (the
        # preparation of the remaining variables and scopes goes on meanwhile)
        tasks: list[asyncio.Task[None]] = []
        for scope in self.scopes():
            for v in VARS.values():
                if self.only is not None and v.id not in self.only:
                    continue
                key = (v.id, scope_id(scope))
                try:
                    target = v.make(self.state.registry)
                    request, payload, context = await prepare(
                        self.state, target, scope, model=self.model, horizon=PAGE_HORIZON, freq="monthly"
                    )
                except Exception as exc:  # e.g. 422: too many gaps — kept as the page's reason
                    reason = getattr(exc, "error", None) or str(exc)
                    if not isinstance(exc, SeriesError) and not hasattr(exc, "error"):
                        log.exception("page forecast could not be prepared", extra={"var": v.id, "scope": key[1]})
                        reason = "The forecast could not be prepared"
                    self.entries[key] = Entry("failed", error=reason)
                    continue
                rec, _ = self.jobs.submit(request, payload, context, background=True)
                prev = self.entries.get(key)
                if prev is None or prev.status != "done":
                    self.entries[key] = Entry("running", run_id=rec.id, studio=studio_link(request))
                tasks.append(asyncio.create_task(finish(key, rec)))

        await asyncio.gather(*tasks)
        log.info(
            "page forecasts computed", extra={"runs": len(tasks), "seconds": round(time.time() - self.round_started)}
        )
        return len(tasks)

    async def keep_fresh(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                log.exception("page forecast round failed")
            await asyncio.sleep(self.state.settings.forecast_page_check_s)

    # ---------------------------------------------------------------- lookup

    def entry(self, var_id: str, scope: ScopeSel) -> Entry:
        v = VARS[var_id]
        if scope_id(scope) not in {scope_id(s) for s in self.scopes()}:
            raise DataUnavailable(
                f"Page forecasts of {v.label} are computed for South Africa and the nine provinces; run this area in "
                "the Forecasting Studio (AI & Predictive Analytics page).",
                missing=[f"forecast:{var_id}:{scope_id(scope)}"],
            )
        e = self.entries.get((var_id, scope_id(scope)))
        if e is None or e.status == "running":
            exc = DataUnavailable(
                f"The {v.label} forecast is being computed (page forecasts are recomputed when new data arrive); try "
                "again in a few minutes.",
                missing=[f"forecast:{var_id}"],
            )
            exc.headers = {"Cache-Control": "no-store"}  # a temporary answer: never cached by browsers or proxies
            raise exc
        if e.status == "failed" or e.result is None:
            raise DataUnavailable(f"No {v.label} forecast: {e.error}", missing=[f"forecast:{var_id}"])
        return e


def studio_link(request: dict[str, Any]) -> str | None:
    """The Forecasting Studio URL that reruns this forecast; None for derived page variables (runoff depth, hot days,
    area in drought, discharge in m³/s), which the Studio cannot request."""
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


def series_set(ctx: Ctx, var_id: str, *, set_id: str, title: str | None = None) -> dict[str, Any]:
    """Observed (last 36 months) + forecast median + 10–90 % band, the divider (`asOf`) at the last observed month."""
    v = VARS[var_id]
    e = pages(ctx.state).entry(var_id, ctx.for_entity().filters.scope)
    r = e.result or {}
    h = horizon_param(ctx)
    hist = [p for p in r["history"] if p[1] is not None][-HISTORY_SHOWN:]
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


def _month_end(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)


def _at(r: dict[str, Any], months: int) -> tuple[float | None, float | None, float | None]:
    fc = r["forecast"]
    if months > len(fc):
        return None, None, None
    _, m, lo, hi = fc[months - 1]
    return m, lo, hi


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
        hist = [p for p in r["history"] if p[1] is not None]
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
        try:
            e = pf.entry(var_id, scope)
        except DataUnavailable:
            rows.append(name)
            values.append([None for _ in RISK_HORIZONS])
            continue
        r = e.result or {}
        cls = r.get("classes") or []
        rows.append(name)
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
