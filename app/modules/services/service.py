"""Water Services & Infrastructure (contract module `services`, spec §18; DB module 18: `18_1` access and coverage,
`18_2` infrastructure operation, both per local municipality and financial year; `18_3` earth-observation extents
per district-year; DECISIONS D46).

District and scope values are sums of household counts and volumes over the municipalities (shares recomputed from
the sums), or area-weighted means of rates. The entities are the 213 local municipalities and metros, whose cards
read their own latest rows. Not in the database: sanitation, treatment plants, reservoirs, pump stations, pipelines,
asset condition, maintenance, projects, planned interruptions, a demand series; those panels stay 501 or null with
the reason. The series are annual, too short for the forecasting engine (monthly / daily only): the forecast keys
answer 501.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core import districts, municipalities
from app.core.provenance import iso
from app.core.stats import rnd
from app.modules.common import Ctx
from app.modules.lm import LocalMunicipalModule
from app.schemas import contract as c

MODULE = "services"
TOTAL_HH = "ws_total_households"
RDP_HH = "ws_rdp_households"
PIPED_HH = "ws_piped_households"
RELIABLE_HH = "ws_reliable_households"
BACKLOG_HH = "ws_backlog_households"
NEW_CONN = "ws_new_connections"
DWELLING = "ws_piped_dwelling_households"
YARD = "ws_piped_yard_households"
STANDPIPE = "ws_standpipe_households"
SCHOOLS = "ws_schools_piped_pct"
CLINICS = "ws_clinics_piped_pct"
INPUT_L = "wo2_system_input_volume"
NRW_L = "wo2_nrw_volume_l"
NRW_PCT = "wo2_nrw_pct"
INTERRUPTIONS = "wo2_unplanned_interruptions"
PRESSURE = "wo2_supply_pressure"
PIPE_FAILURES = "wo2_pipe_failure_rate"
LOAD_SHEDDING = "wo2_load_shedding_hours"
PUMP_ENERGY = "wo2_pump_energy"
SETTLEMENT = "eo_settlement_extent"
INFORMAL = "eo_informal_extent"
RESERVOIRS_KM2 = "eo_reservoir_extent"
NRW_TARGET_PCT = 20.0  # frontend NRW_TARGET_PCT (reference/thresholds.ts)
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
NO_ASSETS = "No infrastructure asset register (plants, reservoirs, pump stations, pipelines) in the database"
ANNUAL = "Annual series (financial years) are too short for the forecasting engine, which needs monthly or daily data"
ACCESS_TABLE = "18_1_water_access_and_coverage_master"
OPS_TABLE = "18_2_infrastructure_operational_master"


class ServicesService(LocalMunicipalModule):
    MODULE = MODULE
    ENTITY_TYPE = "municipality"

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    async def year(self) -> pd.Timestamp:
        return await self.latest_month(TOTAL_HH)

    async def ops_year(self) -> pd.Timestamp:
        return await self.latest_month(INPUT_L)

    async def reliability_year(self) -> pd.Timestamp:
        """The reliable-water columns are one snapshot (April 2026, 149 rows): its date, whatever the year's
        completeness (a snapshot is not a year total)."""
        df = await self.monthly_frame(RELIABLE_HH)
        return pd.Timestamp(df["date"].max()) if not df.empty else await self.year()

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        yr, oy = await self.year(), await self.ops_year()
        ry = await self.reliability_year()
        kpis = [
            self.kpi(
                "served",
                "populationServed",
                "Households Served (RDP level and above)",
                await self.total(RDP_HH, yr),
                "households",
                yr,
                RDP_HH,
                decimals=0,
                sublabel=f"{yr:%Y} (18.1): households at or above RDP level, summed; population is not in the database",
                reason="A sum needs every district of the scope",
            ),
            self.kpi(
                "piped",
                "pipedAccess",
                "Piped Water Access",
                await self.ratio(PIPED_HH, TOTAL_HH, yr),
                "%",
                yr,
                PIPED_HH,
                decimals=1,
                sublabel=f"{yr:%Y}: households with piped infrastructure ÷ total households (18.1)",
            ),
            self.kpi_unavailable(
                "sanitation",
                "sanitationAccess",
                "Sanitation Access (RDP)",
                "%",
                "No sanitation data in the database (18.1 covers water supply)",
            ),
            self.kpi_unavailable("plants", "boreholes", "Water Treatment Plants", "plants", NO_ASSETS),
            self.kpi_unavailable("reservoirs", "boreholes", "Reservoirs", "reservoirs", NO_ASSETS),
            self.kpi_unavailable("pumps", "boreholes", "Pump Stations", "stations", NO_ASSETS),
            self.kpi_unavailable("pipelines", "boreholes", "Pipelines", "km", NO_ASSETS),
            self.kpi(
                "nrw",
                "nrw",
                "Non-Revenue Water",
                await self.ratio(NRW_L, INPUT_L, oy),
                "%",
                oy,
                NRW_L,
                decimals=1,
                sublabel=f"{oy:%Y}: non-revenue volume ÷ system input volume (18.2), target below "
                f"{NRW_TARGET_PCT:.0f} %",
            ),
            self.kpi(
                "reliability",
                "reliability",
                "System Reliability",
                await self.ratio(RELIABLE_HH, TOTAL_HH, ry),
                "%",
                ry,
                RELIABLE_HH,
                decimals=1,
                sublabel=f"{ry:%Y} snapshot: households with reliable water ÷ total households (18.1)",
            ),
        ]
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def years(self, var: str) -> list[pd.Timestamp]:
        df = await self.data.annual(var)
        last = await self.latest_month(var)
        return sorted({pd.Timestamp(pd.Timestamp(d).year, 1, 1) for d in df["date"] if pd.Timestamp(d) <= last})

    async def timeseries(self, metric: str) -> dict[str, Any]:
        if metric == "nrw":
            idx = await self.years(INPUT_L)
            vals = [await self.ratio(NRW_L, INPUT_L, y) for y in idx]
            self.ctx.explain("series.nrw", "No system input volume for this scope")
            return self.series_set(
                "nrw",
                "Non-revenue water (annual, 18.2)",
                [self.series("nrw", "NRW (%)", pd.Series(vals, index=idx, dtype=float), nd=1)],
                [c.Axis(unit="%", label="Non-revenue water", min=0, max=100)],
                [NRW_L, INPUT_L],
                idx[-1] if idx else None,
                ref_lines=[c.RefLine(y=NRW_TARGET_PCT, label=f"Target {NRW_TARGET_PCT:.0f} %")],
                note="Non-revenue volume ÷ system input volume, summed over the scope's municipalities per year.",
            )
        if metric == "interruptions":
            idx = await self.years(INTERRUPTIONS)
            vals = [await self.scope_mean(INTERRUPTIONS, y) for y in idx]
            self.ctx.explain("series.planned", "Planned interruptions are not in the database")
            self.ctx.explain("series.unplanned", "An area-weighted mean needs at least 95 % of the scope's area")
            return self.series_set(
                "interruptions",
                "Unplanned supply interruptions (annual mean, 18.2)",
                [
                    c.Series(key="planned", name="Planned", kind="line", data=[[iso(t.date()), None] for t in idx]),
                    self.series(
                        "unplanned", "Unplanned (events per day)", pd.Series(vals, index=idx, dtype=float), nd=2, i=1
                    ),
                ],
                [c.Axis(unit="events/day", label="Interruptions")],
                [INTERRUPTIONS],
                idx[-1] if idx else None,
                note="18.2 stores unplanned interruptions as events per day (no monthly counts, no planned ones).",
            )
        raise self.no_key("timeseries", metric, ["nrw", "interruptions"], {"plant-performance": NO_ASSETS})

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key(
            "forecast", metric, [], {"nrw": ANNUAL, "coverage": ANNUAL, "demand": "No demand series in the database"}
        )

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        provs = list(districts.PROVINCES.values())
        if metric == "coverage":
            yr = await self.year()
            self.ctx.explain("series.sanitation", "No sanitation data in the database")
            return self.category_set(
                "coverage",
                f"Water access by province – {yr:%Y} (18.1)",
                "%",
                provs,
                [
                    c.CategorySeries(
                        key="piped",
                        name="Piped water access",
                        values=await self.by_province_ratio(PIPED_HH, TOTAL_HH, yr),
                    ),
                    c.CategorySeries(key="sanitation", name="Sanitation access", values=[None] * 9),
                ],
                yr,
                PIPED_HH,
                TOTAL_HH,
            )
        if metric == "service-levels":
            yr = await self.year()
            parts = {
                k: await self.district_values(v, yr)
                for k, v in (("dwelling", DWELLING), ("yard", YARD), ("standpipe", STANDPIPE), ("backlog", BACKLOG_HH))
            }
            series = []
            for key, label in (
                ("dwelling", "Piped (dwelling)"),
                ("yard", "Piped (yard)"),
                ("standpipe", "Communal (< 200 m)"),
                ("backlog", "Below RDP"),
            ):
                vals: list[float | None] = []
                for p in districts.PROVINCES:
                    codes = [d.code for d in districts.in_province(p)]
                    tot = sum(float(parts[k].get(code, 0.0) or 0.0) for k in parts for code in codes)
                    own = sum(float(parts[key].get(code, 0.0) or 0.0) for code in codes)
                    vals.append(rnd(own / tot * 100, 1) if tot > 0 else None)
                series.append(c.CategorySeries(key=key, name=label, values=vals, stack="levels"))
            return self.category_set(
                "service-levels",
                f"Service levels by province (share of households) – {yr:%Y} (18.1)",
                "% of households",
                provs,
                series,
                yr,
                DWELLING,
                YARD,
                STANDPIPE,
                BACKLOG_HH,
            )
        if metric == "urban-rural":
            return await self.urban_rural()
        raise self.no_key(
            "breakdown",
            metric,
            ["coverage", "service-levels", "urban-rural"],
            {
                "asset-condition": NO_ASSETS,
                "maintenance": "No maintenance work orders in the database",
                "pipelines": NO_ASSETS,
                "projects": "No infrastructure projects in the database",
            },
        )

    async def urban_rural(self) -> dict[str, Any]:
        """The April 2026 Urban / Rural rows of 18.1 (the only split the table holds), summed over the scope."""
        ds = self.registry.datasets[ACCESS_TABLE]
        names = [n for code in self.ctx.weights() for n in await self.data.names(ds, code)]
        sql = (
            f'SELECT "Category" AS cat, max({ds.date_sql()}) AS d, sum("Total Households") AS total, '
            f'sum("At or Above RDP Households") AS rdp, sum("Households Below RDP / Backlog (#)") AS backlog, '
            f'avg("Schools with Piped Water (%)") AS schools, avg("Clinics with Piped Water (%)") AS clinics '
            f"FROM {ds.fq} WHERE {ds.not_future_sql()} AND \"Category\" IN ('Urban', 'Rural') "
            f"AND {ds.location_sql()} = ANY($1::text[]) GROUP BY 1"
        )
        rows = {r["cat"]: r for r in await self.ctx.state.db.fetch(sql, names)}
        when = max((pd.Timestamp(r["d"]) for r in rows.values()), default=await self.year())
        cats = ["RDP access (%)", "Backlog (%)", "Schools with piped water (%)", "Clinics with piped water (%)"]
        series = []
        for key in ("Urban", "Rural"):
            r = rows.get(key)
            if r is None or not r["total"]:
                series.append(c.CategorySeries(key=key.lower(), name=key, values=[None] * 4))
                self.ctx.explain(
                    f"series.{key.lower()}", f"No {key.lower()} rows for this scope (18.1 splits April 2026 only)"
                )
                continue
            series.append(
                c.CategorySeries(
                    key=key.lower(),
                    name=key,
                    values=[
                        rnd(float(r["rdp"]) / float(r["total"]) * 100, 1),
                        rnd(float(r["backlog"]) / float(r["total"]) * 100, 1),
                        rnd(r["schools"], 1),
                        rnd(r["clinics"], 1),
                    ],
                )
            )
        return self.category_set(
            "urban-rural", f"Urban and rural access – {when:%b %Y} (18.1)", "%", cats, series, when, TOTAL_HH, RDP_HH
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric != "nrw":
            raise self.no_key("table", metric, ["nrw"], {"plants": NO_ASSETS})
        oy = await self.ops_year()
        nrw, inp = await self.district_values(NRW_L, oy), await self.district_values(INPUT_L, oy)
        rows = []
        sel = self.ctx.filters.scope
        for code in nrw.index:
            d = districts.by_code(str(code))
            if d is None or (sel.level != "national" and code not in sel.district_codes) or code not in inp.index:
                continue
            if pd.isna(nrw[code]) or pd.isna(inp[code]) or float(inp[code]) <= 0:
                continue
            pct = float(nrw[code]) / float(inp[code]) * 100
            rows.append(
                {
                    "id": d.code,
                    "municipality": d.official_name,
                    "province": districts.PROVINCES[d.province],
                    "nrw": rnd(pct, 1),
                    "status": "Above target" if pct > NRW_TARGET_PCT else "Within target",
                }
            )
        rows.sort(key=lambda r: -float(r["nrw"] or 0))
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
        return c.TableSet(
            id="nrw",
            title=f"Non-revenue water by district – {oy:%Y} (18.2)",
            columns=[
                c.Column(key="rank", label="#", type="rank"),
                c.Column(key="municipality", label="District", type="text"),
                c.Column(key="province", label="Province", type="text"),
                c.Column(key="nrw", label="NRW (%)", type="number", decimals=1, align="right", good_direction="down"),
                c.Column(key="status", label="Status", type="text"),
            ],
            rows=rows[: limit or 20],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(oy.date()),
            **self.prov(oy, NRW_L, INPUT_L),
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "coverage":
            return await self.ratio_map(
                layer, "Piped water access (% of households, 18.1)", PIPED_HH, TOTAL_HH, "coverage", await self.year()
            )
        if layer == "rdp":
            return await self.ratio_map(
                layer,
                "Basic (RDP) water access (% of households, 18.1)",
                RDP_HH,
                TOTAL_HH,
                "coverage",
                await self.year(),
            )
        if layer == "nrw":
            return await self.ratio_map(
                layer, "Non-revenue water (% of system input, 18.2)", NRW_L, INPUT_L, "nrw", await self.ops_year()
            )
        if layer == "reliability":
            return await self.ratio_map(
                layer,
                "Supply reliability (% of households, 18.1)",
                RELIABLE_HH,
                TOTAL_HH,
                "coverage",
                await self.reliability_year(),
            )
        raise self.no_key(
            "map",
            layer,
            ["coverage", "rdp", "nrw", "reliability"],
            {"sanitation": "No sanitation data in the database", "asset-condition": NO_ASSETS, "wtp": NO_ASSETS},
        )

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.lm_entities()

    async def entity(self, code: str) -> dict[str, Any]:
        m = municipalities.by_id(code)
        if m is None:
            from app.core.errors import NotFound

            raise NotFound(f"Unknown municipality {code!r}", issues=[{"param": "id"}])
        access, when = await self.lm_latest(
            self.registry.datasets[ACCESS_TABLE],
            m.lm_id,
            cap=await self.year(),
            columns=[
                "Total Households",
                "At or Above RDP Households",
                "Piped Infrastructure Households",
                "Households Below RDP / Backlog (#)",
                "Annual New Connections Installed (#/year)",
                "Schools with Piped Water (%)",
                "Clinics with Piped Water (%)",
            ],
        )
        ops, when2 = await self.lm_latest(
            self.registry.datasets[OPS_TABLE],
            m.lm_id,
            cap=await self.ops_year(),
            columns=[
                "Computed_NRW_Percentage",
                "System_Supply_Pressure_kPa_bar",
                "Pipe_Failure_Rate_breaks_per_100km_year",
                "Load_Shedding_Induced_Interruptions_hours_per_day",
                "Pump_Station_Energy_Consumption_kWh_Ml",
            ],
        )
        total, rdp = access.get("Total Households"), access.get("At or Above RDP Households")
        d = districts.by_code(m.district_code)
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[m.province]),
            c.EntityField(label="District", value=d.official_name if d else m.district_code),
            self.lm_field("Total households", total, "households", when, 0),
            self.lm_field("Households at or above RDP level", rdp, "households", when, 0),
            self.lm_field("RDP access", rdp / total * 100 if rdp is not None and total else None, "%", when, 1),
            self.lm_field(
                "Backlog households", access.get("Households Below RDP / Backlog (#)"), "households", when, 0
            ),
            self.lm_field(
                "New connections", access.get("Annual New Connections Installed (#/year)"), "connections/year", when, 0
            ),
            self.lm_field("Schools with piped water", access.get("Schools with Piped Water (%)"), "%", when, 1),
            self.lm_field("Clinics with piped water", access.get("Clinics with Piped Water (%)"), "%", when, 1),
            self.lm_field("Non-revenue water", ops.get("Computed_NRW_Percentage"), "%", when2, 1),
            self.lm_field("Supply pressure", ops.get("System_Supply_Pressure_kPa_bar"), "kPa", when2, 0),
            self.lm_field(
                "Pipe failure rate", ops.get("Pipe_Failure_Rate_breaks_per_100km_year"), "breaks/100 km/year", when2, 1
            ),
            self.lm_field(
                "Load-shedding interruptions",
                ops.get("Load_Shedding_Induced_Interruptions_hours_per_day"),
                "h/day",
                when2,
                1,
            ),
            self.lm_field("Pump-station energy", ops.get("Pump_Station_Energy_Consumption_kWh_Ml"), "kWh/Ml", when2, 0),
        ]
        if not access and not ops:
            self.ctx.explain("fields", "This municipality has no rows in 18.1 / 18.2")
        latest = max((x for x in (when, when2) if x), default=None)
        return c.EntityDetail(
            id=m.lm_id,
            name=m.name,
            type=self.ENTITY_TYPE,
            province=m.province,
            municipality=m.district_code,
            sub=f"{districts.PROVINCES[m.province]} · {d.official_name if d else m.district_code} · "
            "local municipality record (18.1 / 18.2)",
            title=m.name,
            fields=fields,
            image="municipality",
            as_of=iso(latest),
            source=self.src(TOTAL_HH, NRW_PCT),
        ).dump()


async def prewarm(data: Any) -> None:
    for var in (TOTAL_HH, RDP_HH, PIPED_HH, RELIABLE_HH, BACKLOG_HH, INPUT_L, NRW_L, INTERRUPTIONS):
        await data.annual(var)
