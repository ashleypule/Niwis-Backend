"""Resource Water Quality Objectives (contract module `rwqo`, spec §15; DB module 13: `15.1`, `15.2`, `15.3`;
DECISIONS D43).

One monthly record per district: ecological indices (15.1: habitat integrity, ASPT, FIBI, riparian vegetation,
SASS5, the ecological reserve flow), receiving-water chemistry (15.2) and hydrological objectives (15.3). The tables
state one objective per column (`*_Limit`, identical in every row): DO ≥ 5 mg/L, DIN ≤ 1.5 and TP ≤ 0.12 mg/L,
TDS ≤ 450 mg/L, low flow ≥ 0.8 m³/s, dilution ≥ 2.0 m³/s, assimilation capacity ≥ 50 %. The stored compliance flags
judge copied values and are withheld (D17); the API judges the served values against those objectives
(`thresholds.json` `rwqo_objectives`): the **RWQO compliance** of a district-month is the share of the seven served
objectives met. The Present Ecological State is classed A–F from the habitat-integrity score (Kleynhans 1996 bands).
No monitoring sites or site types in the database: "sites" are districts.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from app.core import districts
from app.core.aggregate import weighted_scalar
from app.core.palettes import SERIES
from app.core.provenance import iso
from app.core.stats import rnd
from app.core.thresholds import pes_class, pes_label, rwqo_pass, thresholds
from app.modules.common import Ctx
from app.modules.compliance import ComplianceService
from app.schemas import contract as c

MODULE = "rwqo"
HABITAT = "eco_aquatic_habitat_integrity"
ASPT = "eco_aspt"
FIBI = "eco_fibi"
SASS5 = "eco_sass5_score"
RIPARIAN = "eco_riparian_vegetation"
RESERVE_Q = "eco_ecological_reserve_q"
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
PES_CLASSES = ["A", "B", "C", "D", "E", "F"]
PES_COLORS = ["#1565C0", "#2E9E5B", "#8BC34A", "#F6CF3E", "#F28C28", "#B2182B"]
SASS5_MAX = 250.0


class RwqoService(ComplianceService):
    MODULE = MODULE
    PARAMS: ClassVar[dict[str, str]] = {
        "do": "rwc_do_value",
        "tn": "rwc_nutrient_value_tn",
        "tp": "rwc_nutrient_value_tp",
        "tds": "rwc_salinity_value_tds",
        "lowFlow": "rwh_low_flow_value",
        "dilution": "rwh_dilution_value",
        "assimilation": "rwh_assimilation_value",
    }
    LIMITS = "rwqo_objectives"
    SCALE = "coverage"
    SCORE_LABEL = "RWQO compliance"
    SCORE_NOTE = (
        "RWQO compliance = share of the seven served objectives (DO, DIN, TP, TDS, low flow, dilution, assimilation "
        "capacity, as stated in the 15.x tables) met by the district that month; the stored flags are withheld "
        "(they judge copied values, D17)."
    )

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    @staticmethod
    def judge(pid: str, value: float) -> bool:
        return rwqo_pass(pid, value)

    @staticmethod
    def band(score: float) -> str:  # the page shows percentages; bands only feed the shared trend panel
        return (
            "Excellent"
            if score >= 90
            else "Good"
            if score >= 70
            else "Moderate"
            if score >= 50
            else "Poor"
            if score >= 30
            else "Critical"
        )

    # ---------------------------------------------------------------- data

    async def eco_month(self) -> pd.Timestamp:
        return min([await self.latest_month(v) for v in (HABITAT, ASPT, FIBI, SASS5)])

    async def pes_by_district(self, month: pd.Timestamp) -> pd.Series:
        return (await self.district_values(HABITAT, month)).dropna().map(lambda v: pes_class(float(v)))

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        last = await self.last()
        em = await self.eco_month()
        score = weighted_scalar(await self.district_scores(last), self.ctx.weights())
        mine = self.mine((await self.scores())[lambda d: d["date"] == last])
        pes = self.pes_in_scope(await self.pes_by_district(em))
        w = self.ctx.weights()
        kpis = [
            self.kpi(
                "sites",
                "boreholes",
                "Districts with RWQO Records",
                float(len(mine)) if len(mine) else None,
                "districts",
                last,
                self.PARAMS["do"],
                decimals=0,
                sublabel="no monitoring sites in the database: one record per district",
            ),
            self.kpi(
                "compliance",
                "rwqoCompliance",
                "RWQO Compliance",
                score,
                "%",
                last,
                self.PARAMS["do"],
                decimals=0,
                sublabel="share of the seven served objectives met" + ("" if len(w) == 1 else " (area-weighted)"),
            ),
            self.kpi(
                "sass5",
                "sass5",
                "Mean SASS5 Score",
                weighted_scalar(await self.district_values(SASS5, em), w),
                None,
                em,
                SASS5,
                decimals=0,
                sublabel=f"{em:%b %Y} (15.1)" + ("" if len(w) == 1 else ", area-weighted"),
            ),
            self.kpi(
                "aspt",
                "aspt",
                "Mean ASPT",
                weighted_scalar(await self.district_values(ASPT, em), w),
                None,
                em,
                ASPT,
                decimals=2,
                sublabel=f"{em:%b %Y} (15.1)" + ("" if len(w) == 1 else ", area-weighted"),
            ),
            self.kpi(
                "fibi",
                "fibi",
                "Fish Index (FIBI)",
                weighted_scalar(await self.district_values(FIBI, em), w),
                None,
                em,
                FIBI,
                decimals=1,
                sublabel=f"{em:%b %Y} (15.1)" + ("" if len(w) == 1 else ", area-weighted"),
            ),
            self.kpi(
                "pes-ab",
                "wqCompliance",
                "Districts in PES A/B",
                float(pes.isin(["A", "B"]).sum()) if len(pes) else None,
                "districts",
                em,
                HABITAT,
                decimals=0,
                sublabel="habitat integrity ≥ 80 (Kleynhans bands)",
            ),
            self.kpi(
                "eflow",
                "eflowCompliance",
                "E-flow Compliance",
                await self.param_share("lowFlow", last),
                "%",
                last,
                self.PARAMS["lowFlow"],
                decimals=0,
                sublabel="districts meeting the low-flow objective (≥ 0.8 m³/s)",
            ),
            self.kpi_unavailable(
                "below-rec",
                "damsBelow",
                "Sites Below REC",
                "sites",
                "Recommended ecological categories are not in the database",
            ),
        ]
        return self.kpi_set(kpis)

    def pes_in_scope(self, per: pd.Series) -> pd.Series:
        sel = self.ctx.filters.scope
        return per if sel.level == "national" else per[per.index.isin(sel.district_codes)]

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        if metric == "ecological":
            em = await self.eco_month()
            frames = {v: await self.monthly_frame(v) for v in (SASS5, FIBI, HABITAT, ASPT)}
            years = sorted({int(pd.Timestamp(d).year) for d in frames[SASS5]["date"] if pd.Timestamp(d) <= em})
            series = []
            for i, (var, name, nd) in enumerate(
                [(SASS5, "SASS5", 0), (FIBI, "FIBI (%)", 1), (HABITAT, "IHI (%)", 1), (ASPT, "ASPT", 2)]
            ):
                df = frames[var]
                vals = []
                for y in years:
                    sub = df[(df["date"].dt.year == y) & (df["date"] <= em)]
                    per = sub.groupby("district")["value"].mean()
                    vals.append(rnd(weighted_scalar(per, self.ctx.weights()), nd))
                series.append(
                    c.Series(
                        key=var.removeprefix("eco_"),
                        name=name,
                        kind="line",
                        color=SERIES[i % len(SERIES)],
                        data=[[str(y), v] for y, v in zip(years, vals, strict=True)],
                    )
                )
            return self.series_set(
                "ecological",
                "Ecological indices by year (15.1, annual means)",
                series,
                [c.Axis(unit="score", label="SASS5 / FIBI / IHI / ASPT")],
                [SASS5, FIBI, HABITAT, ASPT],
                em,
                x_type="category",
                note="Area-weighted means of the district values per year.",
            )
        if metric == "params":
            var = self.PARAMS["do"]
            start, end = await self.window(var)
            s = await self.scope_series(var, start, end)
            return self.series_set(
                "params",
                "Dissolved oxygen (15.2)",
                [self.series("do", "Dissolved oxygen", s, nd=2)],
                [c.Axis(unit="mg/L", label="Dissolved oxygen")],
                [var],
                end,
                ref_lines=[c.RefLine(y=5.0, label="Objective ≥ 5 mg/L")],
            )
        if metric == "eflow":
            var = self.PARAMS["lowFlow"]
            start, end = await self.window(var)
            flow = await self.scope_series(var, start, end)
            ewr = await self.scope_series(RESERVE_Q, start, end)
            return self.series_set(
                "eflow",
                "Low flow against the ecological reserve (15.3 / 15.1)",
                [
                    self.series("flow", "Observed low flow", flow, nd=2, i=0),
                    self.series("ewr", "Ecological flow requirement (EWR)", ewr, nd=2, i=1, dash=True),
                ],
                [c.Axis(unit="m³/s", label="Flow")],
                [var, RESERVE_Q],
                end,
                note="Summed over the scope's districts (every district needed).",
            )
        raise self.no_key("timeseries", metric, ["ecological", "params", "eflow"], {})

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric == "pes":
            em = await self.eco_month()
            pes = self.pes_in_scope(await self.pes_by_district(em))
            if pes.empty:
                self.ctx.explain("series.sites", "No habitat-integrity values for this scope")
            prov = self.ctx.provenance(em.date(), self.src(HABITAT), res="monthly")
            prov.pop("asOf")
            prov["aggregation"] = "district"
            return c.CategorySet(
                id="pes",
                title=f"Districts by Present Ecological State – {em:%b %Y}",
                unit="sites",
                categories=[pes_label(k) for k in PES_CLASSES],
                colors=PES_COLORS,
                series=[
                    c.CategorySeries(
                        key="sites", name="Districts", values=[float((pes == k).sum()) for k in PES_CLASSES]
                    )
                ],
                as_of=iso(em.date()),
                **prov,
            ).dump()
        raise self.no_key(
            "breakdown",
            metric,
            ["pes"],
            {"by-type": "No site types (river, dam, wetland, estuary) in the database: one record per district"},
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric == "results":
            return await self.latest_table("results", "Served values against their objectives", "RWQO")
        if metric == "ranking":
            em = await self.eco_month()
            sass = await self.district_values(SASS5, em)
            aspt = await self.district_values(ASPT, em)
            pes = await self.pes_by_district(em)
            rows = []
            for i, (code, v) in enumerate(
                self.pes_in_scope(sass.dropna()).sort_values(ascending=False).items(), start=1
            ):
                d = districts.by_code(str(code))
                if d is None:
                    continue
                rows.append(
                    {
                        "id": d.code,
                        "rank": i,
                        "site": d.official_name,
                        "type": "district",
                        "sass5": rnd(float(v), 0),
                        "aspt": rnd(aspt.get(code), 2),
                        "pes": pes.get(code),
                    }
                )
            prov = self.ctx.provenance(em.date(), self.src(SASS5, ASPT, HABITAT), res="monthly")
            prov.pop("asOf")
            prov["aggregation"] = "district"
            return c.TableSet(
                id="ranking",
                title=f"Districts by SASS5 score – {em:%b %Y}",
                columns=[
                    c.Column(key="rank", label="#", type="rank"),
                    c.Column(key="site", label="Site (district)", type="text"),
                    c.Column(key="type", label="Type", type="text"),
                    c.Column(key="sass5", label="SASS5", type="number", decimals=0, align="right"),
                    c.Column(key="aspt", label="ASPT", type="number", decimals=2, align="right"),
                    c.Column(key="pes", label="PES", type="class", scale="ecological"),
                ],
                rows=rows[: limit or 20],
                row_key="id",
                link_to="entity",
                total=len(rows),
                as_of=iso(em.date()),
                **prov,
            ).dump()
        raise self.no_key("table", metric, ["results", "ranking"], {})

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "pes":
            em = await self.eco_month()
            pes = await self.pes_by_district(em)
            keep = set(self.ctx.filters.scope.district_codes)
            values = {str(k): v for k, v in pes.items() if k in keep}
            if not values:
                self.ctx.explain("values", f"No habitat-integrity values for these districts in {em:%b %Y}")
            prov = self.ctx.provenance(em.date(), self.src(HABITAT), res="monthly")
            prov["aggregation"] = "district"
            return c.MapLayer(
                id=layer,
                title=f"Present Ecological State (habitat integrity, 15.1) – {em:%b %Y}",
                kind="choropleth",
                level="municipality",
                scale="ecological",
                unit="—",
                date=iso(em.date()) or "",
                values=values,
                geometry="districts",
                **prov,
            ).dump()
        if layer == "compliance":
            return await self.score_map(layer, "RWQO compliance by district")
        if layer == "sass5":
            return await self.district_map(
                layer,
                "SASS5 score (% of 250)",
                SASS5,
                "coverage",
                nd=1,
                transform=lambda v: float(v) / SASS5_MAX * 100,
                unit="%",
            )
        raise self.no_key("map", layer, ["pes", "compliance", "sass5"], {})

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.district_entities("rwqo-site")

    async def entity(self, code: str) -> dict[str, Any]:
        body = await self.entity_card(code, "rwqo-site", "river", "district RWQO record (15.1 – 15.3)")
        em = await self.eco_month()
        pes = (await self.pes_by_district(em)).get(code)
        if pes:
            body["status"] = {"scale": "ecological", "id": pes, "label": pes_label(pes)}
        return body


async def prewarm(data: Any) -> None:
    for var in (*RwqoService.PARAMS.values(), HABITAT, ASPT, FIBI, SASS5):
        await data.monthly(var)


def _classes_note() -> str:
    return str(thresholds()["pes"]["_spec"])
