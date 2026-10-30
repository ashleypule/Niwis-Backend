"""Wastewater Compliance / Green Drop (contract module `green-drop`, spec §13; DB module 11: `13.1`, `13.2`; with
the receiving-water chemistry of `15.2`; DECISIONS D43).

The tables hold one monthly record per district: effluent quality (13.1) and works operation (13.2). No plant
register, so "WWTWs" are districts. The **effluent compliance score** of a district-month is the share of its served
effluent parameters within the General Authorisation limits (`thresholds.json` `effluent_limits`): suspended solids,
ammonia, total phosphorus, E. coli, faecal coliforms and electrical conductivity. 13.1 pH, dissolved oxygen, BOD₅
and COD are withheld (impossible values, D17), so they are not judged. Scores are banded with the frontend's Green
Drop bands and are not official Green Drop assessment scores.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.core.aggregate import weighted_scalar
from app.core.thresholds import effluent_pass, green_drop_band
from app.modules.common import Ctx
from app.modules.compliance import ComplianceService
from app.schemas import contract as c

MODULE = "green-drop"
DESIGN = "wo_design_capacity"  # m³/day
INFLUENT = "wo_influent_flow_rate"  # m³/day
LOADING = "wo_hydraulic_loading_rate"  # %
RECEIVING_DO = "rwc_do_value"
RECEIVING_TN = "rwc_nutrient_value_tn"
RECEIVING_TP = "rwc_nutrient_value_tp"
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
NO_SCORE = "Official Green Drop assessment scores and the plant register are not in the database"
NO_PLANTS = "No WWTW register in the database: one monthly record per district (13.1 / 13.2)"


class GreenDropService(ComplianceService):
    MODULE = MODULE
    PARAMS: ClassVar[dict[str, str]] = {
        "tss": "ww_tss",
        "ammonia": "ww_ammonia_nh3_n",
        "tp": "ww_total_phosphorus",
        "ecoli": "ww_e_coli",
        "faecal": "ww_faecal_coliforms",
        "ec": "ww_ec_salinity",
    }
    LIMITS = "effluent_limits"
    SCALE = "greenDrop"
    SCORE_LABEL = "effluent compliance"
    SCORE_NOTE = (
        "Effluent compliance score = share of the district's served effluent parameters (TSS, ammonia, total "
        "phosphorus, E. coli, faecal coliforms, EC) within the General Authorisation limits that month; pH, DO, BOD₅ "
        "and COD are withheld (impossible values). Not an official Green Drop score."
    )

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    @staticmethod
    def judge(pid: str, value: float) -> bool:
        return effluent_pass(pid, value)

    @staticmethod
    def band(score: float) -> str:
        return green_drop_band(score)

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        last = await self.last()
        per = await self.district_scores(last)
        mine = self.mine((await self.scores())[lambda d: d["date"] == last])
        bands = mine["score"].map(self.band)
        score = weighted_scalar(per, self.ctx.weights())
        design = await self.district_values(DESIGN, await self.latest_month(DESIGN))
        influent = await self.district_values(INFLUENT, await self.latest_month(INFLUENT))
        var = self.PARAMS["tss"]
        kpis = [
            self.kpi(
                "works",
                "boreholes",
                "Districts with WWTW Records",
                float(len(mine)) if len(mine) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel=NO_PLANTS,
            ),
            self.kpi(
                "score",
                "greenDropScore",
                "Effluent Compliance Score",
                score,
                "%",
                last,
                var,
                decimals=1,
                sublabel="share of judged effluent parameters within the general limits"
                + ("" if len(self.ctx.weights()) == 1 else " (area-weighted)"),
                status=self.status(score),
            ),
            self.kpi(
                "good",
                "complianceRate",
                "Excellent / Good",
                float(bands.isin(["Excellent", "Good"]).sum()) if len(mine) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel="score ≥ 70 %",
            ),
            self.kpi(
                "moderate",
                "damsBelow",
                "At Risk (Moderate)",
                float((bands == "Moderate").sum()) if len(mine) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel="score 50–70 %",
            ),
            self.kpi(
                "poor",
                "damsBelow",
                "Non-compliant (Poor / Critical)",
                float(bands.isin(["Poor", "Critical"]).sum()) if len(mine) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel="score below 50 %",
            ),
            self.kpi(
                "capacity",
                "capacity",
                "Total Design Capacity",
                (self.aggregate(design, True) or 0) / 1000 if self.aggregate(design, True) is not None else None,
                "Ml/day",
                await self.latest_month(DESIGN),
                DESIGN,
                decimals=0,
                sublabel="13.2 design capacity, summed over the scope's districts",
                reason="A sum needs every district of the scope",
            ),
            self.kpi(
                "inflow",
                "inflow",
                "Total Influent Flow",
                (self.aggregate(influent, True) or 0) / 1000 if self.aggregate(influent, True) is not None else None,
                "Ml/day",
                await self.latest_month(INFLUENT),
                INFLUENT,
                decimals=0,
                sublabel="13.2 influent flow (treated flow is not stored), summed over the scope's districts",
                reason="A sum needs every district of the scope",
            ),
            self.kpi_unavailable(
                "receiving",
                "wqi",
                "Receiving Water Impact",
                None,
                "No receiving-water index in the database (15.2 holds chemistry: see the receiving series)",
            ),
        ]
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        if metric == "trend":
            return await self.trend("WWTWs (districts)")
        if metric == "capacity":
            start, end = await self.window(DESIGN)
            design = await self.scope_series(DESIGN, start, end)
            influent = await self.scope_series(INFLUENT, start, end)
            loading = await self.scope_series(LOADING, start, end)
            for key in ("design", "influent"):
                self.ctx.explain(f"series.{key}", "A sum needs every district of the scope")
            self.ctx.explain("series.treated", "Treated flow is not in the database (13.2 stores influent flow)")
            return self.series_set(
                "capacity",
                "Works capacity and flow (13.2)",
                [
                    self.series("design", "Design capacity", design / 1000, nd=1, i=0),
                    self.series("influent", "Influent flow", influent / 1000, nd=1, i=1),
                    c.Series(
                        key="treated",
                        name="Treated flow",
                        kind="line",
                        data=[[d, None] for d in [p[0] for p in self.series("x", "x", design).data]],
                    ),
                    self.series("utilisation", "Utilisation (%)", loading, nd=1, i=3, axis=1),
                ],
                [c.Axis(unit="Ml/day", label="Flow"), c.Axis(unit="%", label="Utilisation", min=0)],
                [DESIGN, INFLUENT, LOADING],
                end,
                note="Design capacity and influent flow summed over the scope's districts (m³/day → Ml/day); "
                "utilisation = the hydraulic loading rate (13.2), area-weighted.",
            )
        if metric in ("effluent", "micro", "receiving"):
            spec = {
                "effluent": (
                    "Effluent quality (13.1)",
                    [
                        ("tss", "ww_tss", "TSS"),
                        ("ammonia", "ww_ammonia_nh3_n", "Ammonia (as N)"),
                        ("tp", "ww_total_phosphorus", "Total phosphorus"),
                    ],
                    "mg/L",
                    "BOD₅ and COD are withheld (COD below BOD₅ in nearly every row, D17): suspended solids, "
                    "ammonia and total phosphorus are shown.",
                ),
                "micro": (
                    "Microbiological quality (13.1)",
                    [("ecoli", "ww_e_coli", "E. coli"), ("faecal", "ww_faecal_coliforms", "Faecal coliforms")],
                    "cfu/100 mL",
                    None,
                ),
                "receiving": (
                    "Receiving water chemistry (15.2)",
                    [
                        ("do", RECEIVING_DO, "Dissolved oxygen"),
                        ("tn", RECEIVING_TN, "Total inorganic nitrogen"),
                        ("tp", RECEIVING_TP, "Total phosphorus"),
                    ],
                    "mg/L",
                    "District receiving-water values from the RWQO chemistry table (15.2).",
                ),
            }[metric]
            title, items, unit, note = spec
            start, end = await self.window(items[0][1])
            series = []
            for i, (key, var, name) in enumerate(items):
                s = await self.scope_series(var, start, end)
                series.append(self.series(key, name, s, nd=3, i=i))
            return self.series_set(
                metric,
                title,
                series,
                [c.Axis(unit=unit, label="Concentration")],
                [v for _, v, _ in items],
                end,
                note=note,
            )
        raise self.no_key("timeseries", metric, ["trend", "capacity", "effluent", "micro", "receiving"], {})

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns, tables, maps

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric == "breakdown":
            return await self.band_breakdown("WWTWs (districts)")
        if metric == "by-province":
            return await self.by_province()
        raise self.no_key("breakdown", metric, ["breakdown", "by-province"], {})

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric == "latest":
            return await self.latest_table(
                "latest", "Effluent parameters against the general limits", "Limit / objective"
            )
        if metric == "ranking":
            return await self.ranking_table("Districts by effluent compliance score", "WWTW (district)", limit)
        raise self.no_key("table", metric, ["latest", "ranking"], {})

    async def map(self, layer: str) -> dict[str, Any]:
        if layer in ("compliance", "municipal"):
            return await self.score_map(layer, "Effluent compliance score by district")
        if layer == "utilisation":
            return await self.district_map(layer, "Hydraulic loading rate (13.2)", LOADING, "nrw", nd=1, unit="%")
        raise self.no_key("map", layer, ["compliance", "municipal", "utilisation"], {})

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.district_entities("wwtw")

    async def entity(self, code: str) -> dict[str, Any]:
        return await self.entity_card(code, "wwtw", "wwtw", "district wastewater record (13.1 / 13.2)")


async def prewarm(data: Any) -> None:
    for var in (*GreenDropService.PARAMS.values(), DESIGN, INFLUENT, LOADING):
        await data.monthly(var)
