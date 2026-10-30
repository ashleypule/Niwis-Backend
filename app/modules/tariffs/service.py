"""Water Tariffs & Raw Water Pricing (contract module `tariffs`, spec §19; DB module 19: `19_1` raw water tariffs,
`19_2` municipal tariffs, `19_3` water-quality costs, all per local municipality and year, with the non-revenue
water of `18_2`; DECISIONS D46).

District and scope values are area-weighted means of the municipalities' tariffs and costs (sums for annual costs).
Served: raw abstraction tariff and surcharges, the municipal block tariff, treatment cost and a cost-recovery ratio
(tariff ÷ treatment cost), year-on-year increases, water-quality costs, non-revenue water. Not in the database:
revenues, bulk water tariffs, consumption blocks, household income (affordability); the annual series are too short
for the forecasting engine, so the forecast keys answer 501. Entities: the 213 local municipalities and metros.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core import districts, municipalities
from app.core.provenance import iso
from app.core.stats import rnd
from app.modules.common import Ctx
from app.modules.lm import LocalMunicipalModule
from app.modules.services.service import INPUT_L, NRW_L, PUMP_ENERGY
from app.schemas import contract as c

MODULE = "tariffs"
RAW = "tr_raw_water_tariff"
SURCHARGE = "tr_drought_surcharge"
CAPITAL = "tr_capital_recovery_charge"
STORAGE_TRIGGER = "tr_reservoir_storage_trigger"
IBT_COST = "tr_ibt_cost"
ENERGY_COST = "tr_energy_cost_per_ml"
MAR_RATIO = "tr_mar_demand_ratio"
BLOCK = "tm_block_tariff"
TREATMENT = "tm_treatment_cost"
INFRA_COST = "tm_infrastructure_cost"
CPI = "tm_cpi_ppi"
Q_SOURCE = "tq_source_water_cost"
Q_DOSING = "tq_chemical_dosing_cost"
Q_ALGAL = "tq_algal_bloom_cost"
Q_PENALTY = "tq_wwtw_penalty"
REVENUE_L = "wo2_revenue_water"
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
NO_REVENUE = "Water revenues are not in the database (19.x hold tariffs and costs, not billed volumes or income)"
NO_BULK = "Bulk water tariffs are not in the database (19.1 holds raw water charges, 19.2 municipal tariffs)"
NO_INCOME = "Household income is not in the database, so affordability cannot be computed"
ANNUAL = "Annual series are too short for the forecasting engine, which needs monthly or daily data"
RAW_TABLE = "19_1_raw_water_tariff_master"
MUNI_TABLE = "19_2_municipal_water_tariff_master"
COST_TABLE = "19_3_water_quality_cost_master"
INCREASE_YEARS = 4


class TariffsService(LocalMunicipalModule):
    MODULE = MODULE
    ENTITY_TYPE = "municipality"

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    async def year(self) -> pd.Timestamp:
        return min(await self.latest_month(RAW), await self.latest_month(BLOCK))

    async def years(self, var: str) -> list[pd.Timestamp]:
        df = await self.data.annual(var)
        last = await self.latest_month(var)
        return sorted({pd.Timestamp(pd.Timestamp(d).year, 1, 1) for d in df["date"] if pd.Timestamp(d) <= last})

    async def increase(self, var: str, year: pd.Timestamp) -> pd.Series:
        """Per district: % change of `var` from the previous year."""
        now, prev = (
            await self.district_values(var, year),
            await self.district_values(var, year - pd.DateOffset(years=1)),
        )
        both = now.index.intersection(prev.index)
        out = {}
        for code in both:
            a, b = now[code], prev[code]
            if pd.notna(a) and pd.notna(b) and float(b) > 0:
                out[code] = (float(a) / float(b) - 1) * 100
        return pd.Series(out, dtype=float)

    async def recovery(self, year: pd.Timestamp) -> pd.Series:
        """Per district: municipal tariff ÷ treatment cost per kL (%)."""
        t, cst = await self.district_values(BLOCK, year), await self.district_values(TREATMENT, year)
        both = t.index.intersection(cst.index)
        return pd.Series(
            {
                k: float(t[k]) / float(cst[k]) * 100
                for k in both
                if pd.notna(t[k]) and pd.notna(cst[k]) and float(cst[k]) > 0
            },
            dtype=float,
        )

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        yr = await self.year()
        w = self.ctx.weights()
        note = self.available_note(await self.district_values(BLOCK, yr))
        mean = "" if len(w) == 1 else f" (area-weighted mean, {note})"
        kpis = [
            self.kpi_unavailable("raw-revenue", "revenue", "Raw Water Revenue", "R bn", NO_REVENUE),
            self.kpi_unavailable("bulk-revenue", "revenue", "Bulk Water Revenue", "R bn", NO_REVENUE),
            self.kpi_unavailable("muni-revenue", "revenue", "Municipal Water Revenue", "R bn", NO_REVENUE),
            self.kpi(
                "raw",
                "rawWaterTariff",
                "Average Raw Water Charge",
                await self.scope_mean(RAW, yr),
                "R/m³",
                yr,
                RAW,
                decimals=2,
                sublabel=f"{yr:%Y}: raw water abstraction tariff (19.1){mean}",
            ),
            self.kpi_unavailable("bulk", "tariff", "Average Bulk Water Tariff", "R/kL", NO_BULK),
            self.kpi(
                "municipal",
                "tariff",
                "Average Municipal Tariff",
                await self.scope_mean(BLOCK, yr),
                "R/kL",
                yr,
                BLOCK,
                decimals=2,
                sublabel=f"{yr:%Y}: block tariff rate (19.2){mean}",
            ),
            self.kpi_unavailable("affordability", "affordability", "Affordability", "% of income", NO_INCOME),
            self.kpi(
                "recovery",
                "costRecovery",
                "Cost Recovery Ratio",
                self.mean_available(await self.recovery(yr), w),
                "%",
                yr,
                TREATMENT,
                decimals=0,
                sublabel=f"{yr:%Y}: municipal tariff ÷ average treatment cost per kL (19.2){mean}",
            ),
        ]
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries / forecast

    async def timeseries(self, metric: str) -> dict[str, Any]:
        raise self.no_key("timeseries", metric, [], {})

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key("forecast", metric, [], {"tariff": ANNUAL, "affordability": NO_INCOME, "revenue": NO_REVENUE})

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        provs = list(districts.PROVINCES.values())
        yr = await self.year()
        if metric == "comparison":
            self.ctx.explain("series.bulk", NO_BULK)
            return self.category_set(
                "comparison",
                f"Tariffs by province – {yr:%Y}",
                "R/kL",
                provs,
                [
                    c.CategorySeries(key="raw", name="Raw water charge", values=await self.by_province_mean(RAW, yr)),
                    c.CategorySeries(key="bulk", name="Bulk water tariff", values=[None] * 9),
                    c.CategorySeries(
                        key="municipal", name="Municipal tariff", values=await self.by_province_mean(BLOCK, yr)
                    ),
                ],
                yr,
                RAW,
                BLOCK,
                aggregation="district-area-weighted",
            )
        if metric == "cost-vs-tariff":
            rec = await self.recovery(yr)
            areas = self.ctx.state.spatial.district_areas() or {}
            recovery = [
                rnd(self.mean_available(rec, {d.code: areas.get(d.code, 1.0) for d in districts.in_province(p)}), 0)
                for p in districts.PROVINCES
            ]
            return self.category_set(
                "cost-vs-tariff",
                f"Treatment cost and tariff by province – {yr:%Y} (19.2)",
                "R/kL",
                provs,
                [
                    c.CategorySeries(
                        key="cost",
                        name="Cost of supply (treatment per kL)",
                        values=await self.by_province_mean(TREATMENT, yr),
                    ),
                    c.CategorySeries(
                        key="tariff", name="Average tariff", values=await self.by_province_mean(BLOCK, yr)
                    ),
                    c.CategorySeries(key="recovery", name="Cost recovery (%)", values=recovery, kind="line", axis=1),
                ],
                yr,
                TREATMENT,
                BLOCK,
                aggregation="district-area-weighted",
            )
        if metric == "increases":
            areas = self.ctx.state.spatial.district_areas() or {}

            def prov_mean(per: pd.Series) -> list[float | None]:
                return [
                    rnd(self.mean_available(per, {d.code: areas.get(d.code, 1.0) for d in districts.in_province(p)}), 1)
                    for p in districts.PROVINCES
                ]

            self.ctx.explain("series.bulk", NO_BULK)
            return self.category_set(
                "increases",
                f"Tariff increases by province, {yr.year - 1} → {yr:%Y}",
                "%",
                provs,
                [
                    c.CategorySeries(key="raw", name="Raw water", values=prov_mean(await self.increase(RAW, yr))),
                    c.CategorySeries(key="bulk", name="Bulk water", values=[None] * 9),
                    c.CategorySeries(
                        key="municipal", name="Municipal", values=prov_mean(await self.increase(BLOCK, yr))
                    ),
                ],
                yr,
                RAW,
                BLOCK,
                aggregation="district-area-weighted",
            )
        if metric == "muni-nrw":
            oy = await self.latest_month(INPUT_L)
            return self.category_set(
                "muni-nrw",
                f"System input, billed water and non-revenue water by province – {oy:%Y} (18.2)",
                "Mm³/yr",
                provs,
                [
                    c.CategorySeries(
                        key="input", name="System input volume", values=await self.by_province_sum(INPUT_L, oy, 1e-9, 2)
                    ),
                    c.CategorySeries(
                        key="billed",
                        name="Billed authorised consumption",
                        values=await self.by_province_sum(REVENUE_L, oy, 1e-9, 2),
                    ),
                    c.CategorySeries(
                        key="nrw",
                        name="NRW (%)",
                        values=await self.by_province_ratio(NRW_L, INPUT_L, oy),
                        kind="line",
                        axis=1,
                    ),
                ],
                oy,
                INPUT_L,
                REVENUE_L,
                NRW_L,
            )
        raise self.no_key(
            "breakdown",
            metric,
            ["comparison", "cost-vs-tariff", "increases", "muni-nrw"],
            {
                "revenue": NO_REVENUE,
                "blocks": "Consumption blocks are not in the database (19.2 holds one rate per municipality-year)",
                "affordability": NO_INCOME,
            },
        )

    # ---------------------------------------------------------------- tables / matrix

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric != "ranking":
            raise self.no_key("table", metric, ["ranking"], {"forecast-indicators": ANNUAL})
        yr = await self.year()
        tariff, inc = await self.district_values(BLOCK, yr), await self.increase(BLOCK, yr)
        sel = self.ctx.filters.scope
        rows = []
        for code, v in tariff.dropna().sort_values(ascending=False).items():
            d = districts.by_code(str(code))
            if d is None or (sel.level != "national" and code not in sel.district_codes):
                continue
            rows.append(
                {
                    "id": d.code,
                    "municipality": d.official_name,
                    "province": districts.PROVINCES[d.province],
                    "tariff": rnd(float(v), 2),
                    "increase": rnd(inc.get(code), 1),
                }
            )
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
        self.ctx.explain("rows.increase", "No tariff for the previous year")
        return c.TableSet(
            id="ranking",
            title=f"Districts by municipal tariff – {yr:%Y} (19.2, mean of the municipalities)",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="municipality", label="District", type="text"),
                c.Column(key="province", label="Province", type="text"),
                c.Column(key="tariff", label="Average tariff (R/kL)", type="number", decimals=2, align="right"),
                c.Column(key="increase", label="Increase (%)", type="number", decimals=1, align="right"),
            ],
            rows=rows[: limit or 20],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(yr.date()),
            **self.prov(yr, BLOCK),
        ).dump()

    async def matrix(self, metric: str) -> dict[str, Any]:
        if metric != "province-increase":
            raise self.no_key("matrix", metric, ["province-increase"], {})
        yr = await self.year()
        years = [yr - pd.DateOffset(years=k) for k in reversed(range(INCREASE_YEARS))]
        areas = self.ctx.state.spatial.district_areas() or {}
        cols: list[list[float | None]] = [[] for _ in districts.PROVINCES]
        for y in years:
            inc = await self.increase(BLOCK, y)
            for i, p in enumerate(districts.PROVINCES):
                cols[i].append(
                    rnd(self.mean_available(inc, {d.code: areas.get(d.code, 1.0) for d in districts.in_province(p)}), 1)
                )
        return c.MatrixSet(
            id="province-increase",
            title="Municipal tariff increase by province and year (%)",
            rows=list(districts.PROVINCES.values()),
            cols=[f"{y:%Y}" for y in years],
            values=cols,
            unit="%",
            scale="days",
            as_of=iso(yr.date()),
            **self.prov(yr, BLOCK, aggregation="district-area-weighted"),
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        yr = await self.year()
        if layer == "tariff":
            return await self.district_map(layer, "Average municipal tariff (19.2)", BLOCK, "tariff", nd=2)
        if layer == "increase":
            return await self.value_map(
                layer,
                f"Municipal tariff increase {yr.year - 1} → {yr:%Y}",
                await self.increase(BLOCK, yr),
                "days",
                "%",
                yr,
                BLOCK,
            )
        if layer == "recovery":
            return await self.value_map(
                layer,
                "Cost recovery ratio (tariff ÷ treatment cost)",
                await self.recovery(yr),
                "coverage",
                "%",
                yr,
                BLOCK,
                TREATMENT,
            )
        if layer == "nrw":
            return await self.ratio_map(
                layer,
                "Non-revenue water (% of system input, 18.2)",
                NRW_L,
                INPUT_L,
                "nrw",
                await self.latest_month(INPUT_L),
            )
        raise self.no_key("map", layer, ["tariff", "increase", "recovery", "nrw"], {"affordability": NO_INCOME})

    async def value_map(
        self, layer: str, title: str, per: pd.Series, scale: str, unit: str, year: pd.Timestamp, *variables: str
    ) -> dict[str, Any]:
        keep = set(self.ctx.filters.scope.district_codes)
        out = {str(k): rnd(float(v), 1) for k, v in per.items() if k in keep and pd.notna(v)}
        if not out:
            self.ctx.explain("values", f"No values for these districts in {year:%Y}")
        prov = self.ctx.provenance(year.date(), self.src(*variables), res="annual")
        prov["aggregation"] = "district"
        return c.MapLayer(
            id=layer,
            title=f"{title} – {year:%Y}",
            kind="choropleth",
            level="municipality",
            scale=scale,
            unit=unit,
            date=iso(year.date()) or "",
            values=out,
            geometry="districts",
            **prov,
        ).dump()

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.lm_entities()

    async def entity(self, code: str) -> dict[str, Any]:
        m = municipalities.by_id(code)
        if m is None:
            from app.core.errors import NotFound

            raise NotFound(f"Unknown municipality {code!r}", issues=[{"param": "id"}])
        yr = await self.year()
        raw, when = await self.lm_latest(
            self.registry.datasets[RAW_TABLE],
            m.lm_id,
            cap=yr,
            columns=[
                "Raw water abstraction tariff (R/m³)",
                "Drought-season surcharge (R/m³)",
                "Capital investment recovery charge (R/m³)",
                "MAR_demand_ratio",
            ],
        )
        muni, when2 = await self.lm_latest(
            self.registry.datasets[MUNI_TABLE],
            m.lm_id,
            cap=yr,
            columns=[
                "Block tariff rates (R/kL per consumption block)",
                "Average treatment cost per kL produced (R/kL)",
                "Infrastructure replacement and maintenance cost (R/year)",
            ],
        )
        cost, when3 = await self.lm_latest(
            self.registry.datasets[COST_TABLE], m.lm_id, ["Chemical dosing cost", "WWTW compliance penalty"], cap=yr
        )
        tariff, treat = (
            muni.get("Block tariff rates (R/kL per consumption block)"),
            muni.get("Average treatment cost per kL produced (R/kL)"),
        )
        d = districts.by_code(m.district_code)
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[m.province]),
            c.EntityField(label="District", value=d.official_name if d else m.district_code),
            self.lm_field(
                "Raw water abstraction tariff", raw.get("Raw water abstraction tariff (R/m³)"), "R/m³", when, 2
            ),
            self.lm_field("Drought-season surcharge", raw.get("Drought-season surcharge (R/m³)"), "R/m³", when, 2),
            self.lm_field(
                "Capital recovery charge", raw.get("Capital investment recovery charge (R/m³)"), "R/m³", when, 2
            ),
            self.lm_field("MAR / demand ratio", raw.get("MAR_demand_ratio"), None, when, 2),
            self.lm_field("Municipal block tariff", tariff, "R/kL", when2, 2),
            self.lm_field("Treatment cost per kL", treat, "R/kL", when2, 2),
            self.lm_field(
                "Cost recovery", tariff / treat * 100 if tariff is not None and treat else None, "%", when2, 0
            ),
            self.lm_field(
                "Infrastructure replacement and maintenance",
                muni.get("Infrastructure replacement and maintenance cost (R/year)"),
                "R/year",
                when2,
                0,
            ),
            self.lm_field("Chemical dosing cost", cost.get("Chemical dosing cost"), "R/kL", when3, 2),
            self.lm_field("WWTW compliance penalty", cost.get("WWTW compliance penalty"), "R/year", when3, 0),
        ]
        if not raw and not muni:
            self.ctx.explain("fields", "This municipality has no rows in 19.1 / 19.2")
        latest = max((x for x in (when, when2, when3) if x), default=None)
        return c.EntityDetail(
            id=m.lm_id,
            name=m.name,
            type=self.ENTITY_TYPE,
            province=m.province,
            municipality=m.district_code,
            sub=f"{districts.PROVINCES[m.province]} · {d.official_name if d else m.district_code} · "
            "local municipality record (19.1 – 19.3)",
            title=m.name,
            fields=fields,
            image="municipality",
            as_of=iso(latest),
            source=self.src(RAW, BLOCK, Q_DOSING),
        ).dump()


async def prewarm(data: Any) -> None:
    for var in (RAW, BLOCK, TREATMENT, SURCHARGE, PUMP_ENERGY):
        await data.annual(var)
