"""Water Quality Monitoring (contract module `water-quality`, spec §12; DB module 10: `12.1` – `12.4`).

The four §12 tables hold one monthly record per district (no sampling stations, water-body types or coordinates;
DECISIONS D12). Every column passes the plausibility checks (D16). The API compares the stored values with the
guidelines in `app/core/thresholds.json` (`wq_guidelines`: resource quality objectives for domestic use, DWAF 1996
TWQR / SANS 241, as in the frontend's configuration) — nothing else is derived (D20):

- compliance = share of parameter values within their guideline in the latest complete month;
- "× guideline" maps = value / guideline (the upper limit; for dissolved oxygen, a minimum, guideline / value);
- WQI = the CCME Water Quality Index (CCME 2001) of each district over the trailing 12 months of the guideline
  parameters (F1 scope, F2 frequency, F3 amplitude), classified with the WQI bands of thresholds.json; a scope's WQI is
  the area-weighted mean of its districts' WQI.

Combined metal columns ("Lead/Cadmium/Chromium", "Arsenic/Mercury", "Iron/Manganese") are shown as stored but never
compared with a single-metal guideline. Satellite proxies (12.4) have no units and no guidelines.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app.core import districts
from app.core.palettes import NORMAL, SERIES, YEAR_COLOURS
from app.core.provenance import iso
from app.core.stats import rnd
from app.core.thresholds import thresholds, wqi_class
from app.modules.common import Ctx, Selector
from app.modules.district import NO_POINTS, DistrictModule
from app.modules.rainfall.service import MONTHS
from app.schemas import contract as c

MODULE = "water-quality"
# guideline id → registry variable (single-parameter columns only)
PARAM_VARS: dict[str, str] = {
    "ph": "wqc_ph",
    "ec": "wqp_electrical_conductivity_ec",
    "turbidity": "wqp_turbidity",
    "do": "wqc_dissolved_oxygen_do",
    "bod": "wqc_bod5",
    "cod": "wqc_cod",
    "nitrate": "wqc_nitrate_no3_n",
    "ammonia": "wqc_ammonia_nh3_n",
    "phosphate": "wqc_orthophosphate_po43",
    "ecoli": "wqb_e_coli",
    "chla": "wqb_chlorophyll_a",
    "tds": "wqp_total_dissolved_solids_tds",
    "sulphate": "wqc_sulphate",
    "fluoride": "wqc_fluoride",
}
TREND_PARAMS = ("ec", "ph", "turbidity", "do", "bod", "cod", "nitrate", "phosphate", "ecoli")
RANGE_PARAMS = ("ec", "turbidity", "nitrate", "phosphate", "ammonia", "cod", "ecoli")
GROUPS: dict[str, tuple[str, ...]] = {
    "nutrients": ("wqc_nitrate_no3_n", "wqc_orthophosphate_po43", "wqc_ammonia_nh3_n"),
    "metals": ("wqc_lead_cadmium_chromium", "wqc_arsenic_mercury"),  # one unit (µg/L); Iron/Manganese is in mg/L
    "micro": ("wqb_e_coli", "wqb_total_coliforms"),
}
MAP_PARAMS = ("ph", "do", "ec", "turbidity", "nitrate", "phosphate", "ammonia", "bod", "cod", "ecoli")
PARAM = Selector("param", TREND_PARAMS, "ec")
ORDER = Selector("order", ("poorest", "best"), "poorest")
GROUP = Selector("group", tuple(GROUPS), "nutrients")
SELECTORS: dict[tuple[str, str], tuple[Selector, ...]] = {
    ("timeseries", "param-trend"): (PARAM,),
    ("timeseries", "group"): (GROUP,),
    ("table", "ranking"): (ORDER,),
}
ENTITY_DATASETS = {
    ("timeseries", "param-trend"),
    ("timeseries", "station-wqi"),
    ("timeseries", "group"),
    ("table", "latest"),
}
NO_TYPES = "No water-body type (river, dam, wetland, groundwater) in the database: one record per district"
GUIDE_NOTE = (
    "Guidelines: resource quality objectives for domestic use (DWAF 1996 TWQR / SANS 241), thresholds.json "
    "`wq_guidelines`."
)


def guideline(pid: str) -> dict[str, Any]:
    return next(g for g in thresholds()["wq_guidelines"]["params"] if g["id"] == pid)


def passes(pid: str, v: float) -> bool:
    g = guideline(pid)
    return not (("min" in g and v < g["min"]) or ("max" in g and v > g["max"]))


def ratio(pid: str, v: float) -> float | None:
    """× guideline: value / upper limit, or for a minimum-only guideline (DO) limit / value (> 1 = worse)."""
    g = guideline(pid)
    if g.get("max"):
        return float(v / g["max"])
    if "min" in g and v:
        return float(g["min"] / v)
    return None


def excursion(pid: str, v: float) -> float:
    """CCME excursion of a failed test (0 for a pass)."""
    g = guideline(pid)
    if "max" in g and v > g["max"]:
        return float(v / g["max"] - 1)
    if "min" in g and v < g["min"]:
        return float(g["min"] / v - 1) if v > 0 else 1e6
    return 0.0


def ccme_wqi(tests: list[tuple[str, float]]) -> float | None:
    """CCME WQI (CCME 2001) of (parameter, value) tests: 100 − √(F1² + F2² + F3²) / 1.732. None without tests."""
    if not tests:
        return None
    params = {p for p, _ in tests}
    failed_params = {p for p, v in tests if not passes(p, v)}
    failed = [(p, v) for p, v in tests if not passes(p, v)]
    f1 = len(failed_params) / len(params) * 100
    f2 = len(failed) / len(tests) * 100
    nse = sum(excursion(p, v) for p, v in failed) / len(tests)
    f3 = nse / (0.01 * nse + 0.01)
    return max(0.0, 100 - math.sqrt(f1**2 + f2**2 + f3**2) / 1.732)


class WaterQualityService(DistrictModule):
    MODULE = MODULE

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    def use_entity(self, kind: str, key: str) -> None:
        if (kind, key) in ENTITY_DATASETS and self.ctx.filters.entity_id:
            self.ctx = self.ctx.for_entity()

    def entity_title(self, title: str) -> str:
        return f"{title} — {self.ctx.filters.scope.name}" if self.ctx.filters.entity_id else title

    # ---------------------------------------------------------------- data

    async def last(self) -> pd.Timestamp:
        """Latest complete month common to the guideline parameters."""
        return min([await self.latest_month(v) for v in PARAM_VARS.values()])

    async def frames(self) -> dict[str, pd.DataFrame]:
        last = await self.last()
        out = {}
        for pid, var in PARAM_VARS.items():
            df = await self.monthly_frame(var)
            out[pid] = df[df["date"] <= last]
        return out

    async def wqi_frame(self) -> pd.DataFrame:
        """district, date, value = CCME WQI over the 12 months ending at `date` (months with ≥ 6 sampled months)."""

        async def load() -> pd.DataFrame:
            fr = await self.frames()
            long = pd.concat([df.assign(param=pid) for pid, df in fr.items()], ignore_index=True)
            rows = []
            for code, g in long.groupby("district"):
                months = sorted(g["date"].unique())
                for m in months:
                    m = pd.Timestamp(m)
                    win = g[(g["date"] > m - pd.DateOffset(months=12)) & (g["date"] <= m)]
                    if win["date"].nunique() < 6:
                        continue
                    rows.append((code, m, ccme_wqi(list(zip(win["param"], win["value"].astype(float), strict=True)))))
            return pd.DataFrame(rows, columns=["district", "date", "value"])

        res: pd.DataFrame = await self.ctx.state.cache.get_or_set(
            "wq:wqi", self.ctx.state.settings.cache_ttl_long_series_s, load
        )
        return res

    async def district_wqi(self) -> tuple[pd.Series, pd.Timestamp]:
        df = await self.wqi_frame()
        last = await self.last()
        return df[df["date"] == last].set_index("district")["value"], last

    async def latest_tests(self) -> tuple[pd.DataFrame, pd.Timestamp]:
        """district, param, value, pass for the latest complete month."""
        fr = await self.frames()
        last = await self.last()
        rows = []
        for pid, df in fr.items():
            for code, v in df[df["date"] == last].set_index("district")["value"].items():
                rows.append((code, pid, float(v), passes(pid, float(v))))
        return pd.DataFrame(rows, columns=["district", "param", "value", "pass"]), last

    def mine(self, s: pd.Series | pd.DataFrame) -> Any:
        sel = self.ctx.filters.scope
        if sel.level == "national":
            return s
        if isinstance(s, pd.DataFrame):
            return s[s["district"].isin(sel.district_codes)]
        return s[s.index.isin(sel.district_codes)]

    def prov(self, last: pd.Timestamp, variables: list[str], aggregation: str | None = None) -> dict[str, Any]:
        p = self.ctx.provenance(last.date(), self.src(*variables), res="monthly")
        p.pop("asOf")
        if aggregation:
            p["aggregation"] = aggregation
        return p

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        tests, last = await self.latest_tests()
        t = self.mine(tests)
        wqi, _ = await self.district_wqi()
        w = self.mine(wqi.dropna())
        scope_wqi = self.aggregate(wqi, False)
        cls = wqi_class(scope_wqi) if scope_wqi is not None else None
        var = PARAM_VARS["ec"]
        exceed = t.groupby("district")["pass"].apply(lambda s: (~s).any()) if len(t) else pd.Series(dtype=bool)
        kpis = [
            self.kpi_unavailable("stations", "boreholes", "Monitoring Stations", "stations", NO_POINTS),
            self.kpi(
                "compliance",
                "wqCompliance",
                "Compliance Rate",
                float(t["pass"].mean() * 100) if len(t) else None,
                "%",
                last,
                var,
                decimals=0,
                sublabel=f"{last:%b %Y}: share of {len(PARAM_VARS)} parameters × "
                f"{t['district'].nunique() if len(t) else 0} districts within guideline",
            ),
            self.kpi(
                "exceeding",
                "stationsExceeding",
                "Districts Exceeding Limits",
                float(exceed.sum()) if len(exceed) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel="at least one parameter outside its guideline",
            ),
            self.kpi(
                "wqi",
                "wqi",
                "Average WQI",
                scope_wqi,
                None,
                last,
                var,
                decimals=0,
                sublabel="CCME WQI, 12 months" + ("" if len(self.ctx.weights()) == 1 else " (area-weighted)"),
                status=c.Status(scale="wqi", id=cls, label=cls) if cls else None,
            ),
            self.kpi(
                "poor",
                "stationsExceeding",
                "Districts in Poor Quality",
                float(sum(1 for v in w if wqi_class(float(v)) in ("Poor", "Very Poor"))) if len(w) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel="WQI below 50 (Poor or Very Poor)",
            ),
            self.kpi_unavailable("dams", "wqCompliance", "Dams with Good Quality", "%", NO_TYPES),
            self.kpi_unavailable("groundwater", "wqCompliance", "Groundwater Quality", "%", NO_TYPES),
            self.kpi(
                "risk",
                "boreholesAtRisk",
                "High-Risk Districts",
                float(sum(1 for v in w if wqi_class(float(v)) == "Very Poor")) if len(w) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel="WQI below 25 (Very Poor)",
            ),
        ]
        all_src = self.src(*PARAM_VARS.values())
        for k in kpis:
            if k.source:  # the guideline comparisons read every guideline parameter, not only EC
                k.source = all_src
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        self.use_entity("timeseries", metric)
        if metric == "param-trend":
            return await self.ts_param_trend()
        if metric == "station-wqi":
            return await self.ts_wqi()
        if metric == "group":
            return await self.ts_group()
        raise self.no_key("timeseries", metric, ["param-trend", "station-wqi", "group"], {})

    async def ts_param_trend(self) -> dict[str, Any]:
        pid = self.ctx.select(PARAM)
        var = PARAM_VARS[pid]
        s = await self.scope_series(var)
        last = await self.latest_month(var)
        g = guideline(pid)
        series = []
        for i, yr in enumerate((last.year, last.year - 1)):
            ys = s[s.index.year == yr]
            data = [[MONTHS[m - 1], rnd(ys.get(pd.Timestamp(yr, m, 1)), 3)] for m in range(1, 13)]
            series.append(
                c.Series(key=str(yr), name=str(yr), kind="line", color=YEAR_COLOURS[i], data=data, symbol=True)
            )
        unit = self.unit(var)
        bound = float(g.get("max", g.get("min", 0)))
        return self.series_set(
            "param-trend",
            self.entity_title(f"{self.registry.variables[var].label} by month"),
            series,
            [c.Axis(unit=unit if unit != "—" else "pH", label=self.registry.variables[var].label)],
            [var],
            last,
            x_type="category",
            note=f"Monthly values as stored ({'district value' if len(self.ctx.weights()) == 1 else 'area-weighted'}); "
            + GUIDE_NOTE,
            refLines=[c.RefLine(y=bound, label=f"Guideline {'≥' if 'min' in g and 'max' not in g else '≤'} {bound:g}")],
        )

    async def ts_wqi(self) -> dict[str, Any]:
        df = await self.wqi_frame()
        from app.core.aggregate import weighted

        g = weighted(df, self.ctx.weights()).set_index("date")["value"].dropna().sort_index()
        start, end = await self.window(PARAM_VARS["ec"])
        win = g[(g.index >= start) & (g.index <= end)]
        mean = g.mean() if len(g) else None
        if win.empty:
            self.ctx.explain("series.wqi", "No WQI for this scope in the selected period (12 months of data needed)")
        return self.series_set(
            "station-wqi",
            self.entity_title("Water Quality Index (CCME)"),
            [
                self.series("wqi", "WQI", win, nd=1, i=0),
                c.Series(
                    key="lt",
                    name="Long-term average",
                    kind="line",
                    color=NORMAL,
                    dash=True,
                    data=[[iso(t.date()), rnd(mean, 1)] for t in win.index],
                ),
            ],
            [c.Axis(unit="WQI", label="WQI", min=0, max=100)],
            [PARAM_VARS["ec"], PARAM_VARS["ecoli"], PARAM_VARS["ph"]],
            end,
            note="CCME Water Quality Index (2001) of the trailing 12 months of 14 parameters against their guidelines; "
            + GUIDE_NOTE,
        )

    async def ts_group(self) -> dict[str, Any]:
        key = self.ctx.select(GROUP)
        variables = GROUPS[key]
        start, end = await self.window(variables[0])
        series = []
        for i, var in enumerate(variables):
            s = await self.scope_series(var, start, end)
            series.append(
                self.series(
                    var,
                    f"{self.registry.variables[var].label} ({self.unit(var)})",
                    s,
                    nd=3,
                    i=i,
                    source=self.ctx.col_source(var).get("source"),
                )
            )
        unit = self.unit(variables[0])
        title = {"nutrients": "Nutrients", "metals": "Metals (combined columns, as stored)", "micro": "Microbiology"}[
            key
        ]
        return self.series_set(
            "group",
            self.entity_title(title),
            series,
            [c.Axis(unit=unit, label="Concentration" if key != "micro" else "Count", log=key == "micro")],
            list(variables),
            end,
            note="Monthly values as stored in 12.2 / 12.3"
            + (" (Iron/Manganese is in mg/L: it is in the database view)" if key == "metals" else ""),
        )

    async def forecast(self, metric: str) -> dict[str, Any]:
        if metric in ("wqi", "params", "ecoli-catchment"):
            raise self.forecast_none(metric)
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        tests, last = await self.latest_tests()
        t = self.mine(tests)
        wqi, _ = await self.district_wqi()
        w = self.mine(wqi.dropna())
        classes = ["Excellent", "Good", "Fair", "Poor", "Very Poor"]
        colors = ["#1565C0", "#2E9E5B", "#F6CF3E", "#F28C28", "#B2182B"]
        if metric == "param-range":
            cats, mins, avgs, maxs, guide = [], [], [], [], []
            for pid in RANGE_PARAMS:
                v = t[t["param"] == pid]["value"]
                g = guideline(pid)
                cats.append(f"{g['label']} ({g['unit']})")
                mins.append(rnd(v.min(), 3) if len(v) else None)
                avgs.append(rnd(v.mean(), 3) if len(v) else None)
                maxs.append(rnd(v.max(), 3) if len(v) else None)
                guide.append(float(g.get("max", g.get("min", 0))))
            series = [
                c.CategorySeries(key="min", name="Minimum", values=mins, color=SERIES[1]),
                c.CategorySeries(key="avg", name="Average", values=avgs, color=SERIES[0]),
                c.CategorySeries(key="max", name="Maximum", values=maxs, color=SERIES[3]),
                c.CategorySeries(key="guideline", name="Guideline", values=guide, kind="line", color=NORMAL),
            ]
            return c.CategorySet(
                id="param-range",
                title=f"Parameter range across districts – {last:%b %Y}",
                unit="mixed",
                units=[guideline(p)["unit"] for p in RANGE_PARAMS],
                categories=cats,
                series=series,
                as_of=iso(last.date()),
                **self.prov(last, [PARAM_VARS[p] for p in RANGE_PARAMS], "district"),
            ).dump()
        if metric == "compliance":
            ok = t.groupby("district")["pass"].all() if len(t) else pd.Series(dtype=bool)
            return c.CategorySet(
                id="compliance",
                title=f"Districts within every guideline – {last:%b %Y}",
                unit="districts",
                categories=["Compliant", "Not compliant"],
                colors=["#2E9E5B", "#B2182B"],
                series=[c.CategorySeries(key="d", name="Districts", values=[float(ok.sum()), float((~ok).sum())])],
                as_of=iso(last.date()),
                **self.prov(last, list(PARAM_VARS.values()), "district"),
            ).dump()
        if metric == "wqi-distribution":
            counts = [float(sum(1 for v in w if wqi_class(float(v)) == k)) for k in classes]
            return c.CategorySet(
                id="wqi-distribution",
                title=f"Districts by WQI class – {last:%b %Y}",
                unit="districts",
                categories=classes,
                colors=colors,
                series=[c.CategorySeries(key="d", name="Districts", values=counts)],
                as_of=iso(last.date()),
                **self.prov(last, list(PARAM_VARS.values()), "district"),
            ).dump()
        if metric == "by-province":
            provs = list(districts.PROVINCES)
            series = []
            for k, col in zip(classes, colors, strict=True):
                vals: list[float | None] = []
                for pv in provs:
                    codes = {d.code for d in districts.in_province(pv)}
                    vals.append(
                        float(sum(1 for code, v in wqi.dropna().items() if code in codes and wqi_class(float(v)) == k))
                    )
                series.append(
                    c.CategorySeries(key=k.lower().replace(" ", "-"), name=k, values=vals, color=col, stack="wqi")
                )
            return c.CategorySet(
                id="by-province",
                title=f"Districts by WQI class per province – {last:%b %Y}",
                unit="districts",
                categories=provs,
                series=series,
                as_of=iso(last.date()),
                **self.prov(last, list(PARAM_VARS.values()), "district"),
            ).dump()
        if metric == "param-compliance":
            pids = list(PARAM_VARS)[:8]
            shares: list[float | None] = [
                rnd(float(t[t["param"] == p]["pass"].mean() * 100), 0) if len(t[t["param"] == p]) else None
                for p in pids
            ]
            return c.CategorySet(
                id="param-compliance",
                title=f"Districts within guideline per parameter – {last:%b %Y}",
                unit="%",
                categories=[guideline(p)["label"] for p in pids],
                series=[c.CategorySeries(key="share", name="Districts within guideline", values=shares)],
                as_of=iso(last.date()),
                **self.prov(last, [PARAM_VARS[p] for p in pids], "district"),
            ).dump()
        raise self.no_key(
            "breakdown",
            metric,
            ["param-range", "compliance", "wqi-distribution", "by-province", "param-compliance"],
            {"by-type": NO_TYPES, "risk-outlook": "Water-quality outlooks need a forecast model"},
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        self.use_entity("table", metric)
        if metric == "latest":
            return await self.tbl_latest()
        if metric == "hotspots":
            return await self.tbl_hotspots(limit)
        if metric == "ranking":
            return await self.tbl_ranking(limit)
        raise self.no_key(
            "table",
            metric,
            ["latest", "hotspots", "ranking"],
            {"counts": NO_TYPES, "forecast-indicators": "Water-quality outlooks need a forecast model"},
        )

    async def tbl_latest(self) -> dict[str, Any]:
        rows = []
        last = await self.last()
        for pid, var in PARAM_VARS.items():
            v = await self.scope_value(var, last)
            g = guideline(pid)
            limit = (
                f"{g['min']:g}–{g['max']:g}"
                if "min" in g and "max" in g
                else (f"≤ {g['max']:g}" if "max" in g else f"≥ {g['min']:g}")
            )
            rows.append(
                {
                    "id": pid,
                    "parameter": g["label"],
                    "value": rnd(v, 3),
                    "unit": g["unit"],
                    "guideline": limit,
                    "status": None if v is None else ("Within guideline" if passes(pid, v) else "Exceeds guideline"),
                }
            )
        self.ctx.explain("rows.value", "No values for this scope in the latest complete month")
        self.ctx.explain("rows.status", "No values for this scope in the latest complete month")
        return c.TableSet(
            id="latest",
            title=self.entity_title(f"Latest results – {last:%b %Y}"),
            columns=[
                c.Column(key="parameter", label="Parameter", type="text"),
                c.Column(key="value", label="Value", type="number", decimals=3, align="right"),
                c.Column(key="unit", label="Unit", type="text"),
                c.Column(key="guideline", label="Guideline", type="text"),
                c.Column(key="status", label="Status", type="text"),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(last.date()),
            **self.prov(last, list(PARAM_VARS.values())),
        ).dump()

    async def tbl_hotspots(self, limit: int | None) -> dict[str, Any]:
        tests, last = await self.latest_tests()
        t = self.mine(tests)
        bad = t[~t["pass"]].copy()
        bad["r"] = [ratio(p, v) or 0 for p, v in zip(bad["param"], bad["value"], strict=True)]
        rows = []
        for i, r in enumerate(bad.sort_values("r", ascending=False).itertuples(), 1):
            d = districts.by_code(str(r.district))
            g = guideline(str(r.param))
            rows.append(
                {
                    "id": f"{r.district}-{r.param}",
                    "rank": i,
                    "location": d.official_name if d else r.district,
                    "water": None,
                    "parameter": g["label"],
                    "value": rnd(float(r.value), 3),
                    "limit": f"{'≥' if 'min' in g and 'max' not in g else '≤'} "
                    f"{g.get('max', g.get('min')):g} {g['unit']}",
                    "status": f"{rnd(float(r.r), 1)} × guideline",
                }
            )
        self.ctx.explain("rows.water", NO_TYPES)
        return c.TableSet(
            id="hotspots",
            title=f"Parameters outside guideline – {last:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="location", label="District", type="text"),
                c.Column(key="water", label="Water body", type="text"),
                c.Column(key="parameter", label="Parameter", type="text"),
                c.Column(key="value", label="Value", type="number", decimals=3, align="right"),
                c.Column(key="limit", label="Limit", type="text"),
                c.Column(key="status", label="Status", type="text"),
            ],
            rows=rows[: limit or 15],
            row_key="id",
            total=len(rows),
            as_of=iso(last.date()),
            **self.prov(last, list(PARAM_VARS.values()), "district"),
        ).dump()

    async def tbl_ranking(self, limit: int | None) -> dict[str, Any]:
        wqi, last = await self.district_wqi()
        df = await self.wqi_frame()
        prev = df[df["date"] == last - pd.DateOffset(years=1)].set_index("district")["value"]
        best = self.ctx.select(ORDER) == "best"
        rows = []
        for i, (code, v) in enumerate(self.mine(wqi.dropna()).sort_values(ascending=not best).items(), 1):
            d = districts.by_code(str(code))
            p = prev.get(code)
            rows.append(
                {
                    "id": str(code),
                    "rank": i,
                    "station": d.official_name if d else code,
                    "river": None,
                    "wqi": rnd(float(v), 0),
                    "status": wqi_class(float(v)),
                    "trend": rnd(float(v) - float(p), 1) if p is not None and pd.notna(p) else None,
                }
            )
        self.ctx.explain("rows.river", NO_TYPES)
        self.ctx.explain("rows.trend", "No WQI 12 months earlier")
        return c.TableSet(
            id="ranking",
            title=f"Districts by WQI ({'best' if best else 'poorest'} first) – {last:%b %Y}",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="station", label="District", type="text"),
                c.Column(key="river", label="River", type="text"),
                c.Column(key="wqi", label="WQI", type="number", decimals=0, align="right"),
                c.Column(key="status", label="Status", type="class", scale="wqi"),
                c.Column(key="trend", label="vs 12 months ago", type="delta", decimals=1, align="right"),
            ],
            rows=rows[: limit or 10],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(last.date()),
            **self.prov(last, list(PARAM_VARS.values()), "district"),
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "wqi":
            wqi, last = await self.district_wqi()
            return self._map(layer, f"Water Quality Index (CCME, 12 months) – {last:%b %Y}", wqi, last, "wqi", "—", 0)
        if layer in MAP_PARAMS:
            var = PARAM_VARS[layer]
            last = await self.last()
            vals = await self.district_values(var, last)
            r = pd.Series({k: ratio(layer, float(v)) for k, v in vals.dropna().items()}, dtype=float)
            g = guideline(layer)
            return self._map(
                layer, f"{g['label']} relative to guideline – {last:%b %Y}", r, last, "ratio", "× guideline", 2, var
            )
        raise self.no_key(
            "map",
            layer,
            ["wqi", *MAP_PARAMS],
            {
                "lead": "The database stores lead only combined with cadmium and chromium ('Lead/Cadmium/Chromium'): "
                "a single-metal guideline does not apply",
                "forecast-wqi": "Water-quality forecasts need a forecast model",
            },
        )

    def _map(
        self,
        layer: str,
        title: str,
        vals: pd.Series,
        last: pd.Timestamp,
        scale: str,
        unit: str,
        nd: int,
        var: str | None = None,
    ) -> dict[str, Any]:
        out = {str(k): rnd(float(v), nd) for k, v in self.mine(vals.dropna()).items()}
        if not out:
            self.ctx.explain("values", "No district of this scope has values")
        p = self.ctx.provenance(
            last.date(), self.src(var or PARAM_VARS["ec"]) if var else self.src(*PARAM_VARS.values()), res="monthly"
        )
        p["aggregation"] = "district"
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
            **p,
        ).dump()

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.district_entities("district")

    async def entity(self, code: str) -> dict[str, Any]:
        d = self.district(code)
        wqi, last = await self.district_wqi()
        v = wqi.get(d.code)
        cls = wqi_class(float(v)) if v is not None and pd.notna(v) else None
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            c.EntityField(label=f"WQI ({last:%b %Y})", value=rnd(v, 0) if cls else None),
        ]
        for pid in ("ec", "ph", "turbidity", "do", "nitrate", "phosphate", "ecoli"):
            fields.append(await self.field(guideline(pid)["label"], PARAM_VARS[pid], d.code, nd=2))
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type="district",
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code} · district water-quality record (12.x)",
            title=d.official_name,
            fields=fields,
            image="station",
            **({"status": c.Status(scale="wqi", id=cls, label=cls)} if cls else {}),
            as_of=iso(last.date()),
            source=self.src(*PARAM_VARS.values()),
        ).dump()


async def prewarm(data: Any) -> None:
    for var in {*PARAM_VARS.values(), *(v for g in GROUPS.values() for v in g)}:
        await data.monthly(var)
