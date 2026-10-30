"""Drinking Water Compliance / Blue Drop (contract module `blue-drop`, spec §14; DB module 12: `14.1`;
DECISIONS D43).

`14.1` holds one monthly record per district with SANS 241 determinands. Its turbidity, temperature, pH, EC, E. coli,
free chlorine, lead and cyanide values are copies or rescalings of the 13.1 wastewater effluent values and are
withheld, as are the stored compliance flags and score that judge them (D17). The served determinands — colour,
total coliforms, nitrate, fluoride, arsenic and total trihalomethanes — are compared with SANS 241:2015
(`thresholds.json` `sans241`); the **SANS 241 compliance score** of a district-month is the share of them within
their limit, banded with the frontend's Blue Drop bands. It is not an official Blue Drop assessment score, and there
are no water-supply systems in the database: "systems" are districts.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.core.aggregate import weighted_scalar
from app.core.thresholds import blue_drop_band, sans241_pass
from app.modules.common import Ctx
from app.modules.compliance import ComplianceService
from app.schemas import contract as c

MODULE = "blue-drop"
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
NO_SYSTEMS = "No water-supply-system register in the database: one monthly record per district (14.1)"
COPIED = "withheld: the 14.1 value is a copy of the 13.1 wastewater effluent value in every district-month (D17)"


class BlueDropService(ComplianceService):
    MODULE = MODULE
    PARAMS: ClassVar[dict[str, str]] = {
        "colour": "sans_colour_value",
        "totalColiforms": "sans_total_coliforms_value",
        "nitrate": "sans_nitrate_value",
        "fluoride": "sans_fluoride_value",
        "arsenic": "sans_arsenic_value",
        "thm": "sans_total_thms_value",
    }
    LIMITS = "sans241"
    SCALE = "blueDrop"
    SCORE_LABEL = "SANS 241 compliance"
    SCORE_NOTE = (
        "SANS 241 compliance score = share of the district's served determinands (colour, total coliforms, nitrate, "
        "fluoride, arsenic, total THMs) within SANS 241:2015 that month; turbidity, temperature, pH, EC, E. coli, "
        "free chlorine, lead and cyanide are withheld (copies of wastewater effluent values). Not an official Blue "
        "Drop score."
    )

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    @staticmethod
    def judge(pid: str, value: float) -> bool:
        return sans241_pass(pid, value)

    @staticmethod
    def band(score: float) -> str:
        return blue_drop_band(score)

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        last = await self.last()
        per = await self.district_scores(last)
        mine = self.mine((await self.scores())[lambda d: d["date"] == last])
        bands = mine["score"].map(self.band)
        score = weighted_scalar(per, self.ctx.weights())
        tests = self.mine(await self.tests())
        n_tests = int((tests["date"] == last).sum()) if len(tests) else 0
        micro = await self.param_share("totalColiforms", last)
        var = self.PARAMS["nitrate"]
        kpis = [
            self.kpi(
                "systems",
                "boreholes",
                "Districts with Drinking-water Records",
                float(len(mine)) if len(mine) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel=NO_SYSTEMS,
            ),
            self.kpi(
                "score",
                "blueDropScore",
                "SANS 241 Compliance Score",
                score,
                "%",
                last,
                var,
                decimals=1,
                sublabel="share of served determinands within SANS 241"
                + ("" if len(self.ctx.weights()) == 1 else " (area-weighted)"),
                status=self.status(score),
            ),
            self.kpi(
                "risk",
                "damsBelow",
                "Districts at High Risk",
                float(bands.isin(["Poor", "Critical"]).sum()) if len(mine) else None,
                "districts",
                last,
                var,
                decimals=0,
                sublabel="score below 70 %",
            ),
            self.kpi_unavailable(
                "plants", "boreholes", "Treatment Plants", "plants", "No treatment-plant register in the database"
            ),
            self.kpi(
                "samples",
                "recordsIngested",
                "Values Judged",
                float(n_tests) if n_tests else None,
                "values",
                last,
                var,
                decimals=0,
                sublabel=f"{last:%b %Y}: served determinands × districts",
            ),
            self.kpi(
                "micro",
                "wqCompliance",
                "Microbiological Compliance",
                micro,
                "%",
                last,
                self.PARAMS["totalColiforms"],
                decimals=0,
                sublabel="districts with total coliforms at 0 cfu/100 mL (E. coli is withheld: " + COPIED + ")",
            ),
            self.kpi_unavailable(
                "population", "populationServed", "Population Served", "people", "No population data in the database"
            ),
            self.kpi(
                "status",
                "wqi",
                "Overall Drinking Water Status",
                score,
                "%",
                last,
                var,
                decimals=0,
                sublabel="SANS 241 compliance band (frontend Blue Drop bands)",
                status=self.status(score),
            ),
        ]
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        if metric == "trend":
            return await self.trend("systems (districts)")
        if metric == "params":
            items = [
                ("nitrate", "sans_nitrate_value", "Nitrate (mg/L)"),
                ("fluoride", "sans_fluoride_value", "Fluoride (mg/L)"),
                ("thm", "sans_total_thms_value", "Total THMs (mg/L)"),
            ]
            start, end = await self.window(items[0][1])
            series = [
                self.series(k, n, await self.scope_series(v, start, end), nd=3, i=i)
                for i, (k, v, n) in enumerate(items)
            ]
            return self.series_set(
                "params",
                "Served SANS 241 determinands (14.1)",
                series,
                [c.Axis(unit="mg/L", label="Value")],
                [v for _, v, _ in items],
                end,
                note="pH, turbidity and free chlorine are " + COPIED + "; nitrate, fluoride and total THMs are shown.",
            )
        raise self.no_key(
            "timeseries",
            metric,
            ["trend", "params"],
            {"performance": "Treatment-plant flows are not in the database (13.2 holds wastewater works)"},
        )

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns, tables, maps

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric == "breakdown":
            return await self.band_breakdown("systems (districts)")
        if metric == "by-province":
            return await self.by_province()
        if metric == "param-compliance":
            return await self.param_compliance("Districts within SANS 241 per determinand")
        raise self.no_key("breakdown", metric, ["breakdown", "by-province", "param-compliance"], {})

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric == "latest":
            return await self.latest_table("latest", "Served determinands against SANS 241", "SANS 241 limit")
        if metric == "ranking":
            return await self.ranking_table("Districts by SANS 241 compliance score", "System (district)", limit)
        raise self.no_key("table", metric, ["latest", "ranking"], {})

    async def map(self, layer: str) -> dict[str, Any]:
        if layer in ("compliance", "municipal"):
            return await self.score_map(layer, "SANS 241 compliance score by district")
        raise self.no_key("map", layer, ["compliance", "municipal"], {"ecoli": "E. coli " + COPIED})

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.district_entities("wss")

    async def entity(self, code: str) -> dict[str, Any]:
        return await self.entity_card(code, "wss", "wtp", "district drinking-water record (14.1)")


async def prewarm(data: Any) -> None:
    for var in BlueDropService.PARAMS.values():
        await data.monthly(var)
