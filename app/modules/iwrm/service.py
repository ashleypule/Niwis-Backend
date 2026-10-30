"""Water Resource Management / IWRM (contract module `iwrm`, spec §16; DB module 14: `16_1`, with `17_1` transfers
and the groundwater-reserve tables; DECISIONS D42).

`16_1` holds one record per district-year (2000–2025) of the water-resource matrix: mean annual precipitation,
evapotranspiration and runoff, the surface-water availability index, storage efficiency, groundwater recharge and
dependency, inter-basin transfer volumes, a water balance, sector demands (municipal per capita, agricultural per
hectare, industrial per year), system losses, the ecological reserve requirement and return / recycled flows.

Served: availability (mean annual runoff + groundwater recharge × district area, Mm³/year, summed over the scope),
the reserve requirement, return flows, transfers, system losses and the stored water balance. Not derivable: total
demand in volume (per-capita and per-hectare demands need population and irrigated area, which are not stored), hence
the supply–demand balance, the water stress index and sector shares; catchment yields; a programme status.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core import districts
from app.core.aggregate import sum_fractions, weighted_scalar
from app.core.filters import parse_scope
from app.core.provenance import iso
from app.core.stats import rnd
from app.modules.common import Ctx
from app.modules.district import DistrictModule
from app.modules.water_quantity.service import SCHEMES, WaterQuantityService
from app.schemas import contract as c

MODULE = "iwrm"
MAR = "wrm_mean_annual_runoff_mar_million_m3_yr"  # Mm³/year
RECHARGE = "wrm_mean_annual_groundwater_recharge_mm_yr"  # mm/year
IBT = "wrm_inter_basin_transfer_volumes_million_m3_yr"  # Mm³/year
BALANCE = "wrm_national_water_balance_million_m3_yr"  # Mm³/year, one value per district as stored
INDUSTRIAL = "wrm_industrial_water_demand_m3_yr"  # m³/year
LOSSES = "wrm_system_water_losses_pct"  # %
RESERVE = "wrm_ecological_reserve_water_requirement_m3_yr"  # m³/year
RETURN_FLOW = "wrm_return_flow_recycled_water_million_m3_yr"  # Mm³/year
SWAI = "wrm_surface_water_availability_index_ratio"
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
NO_DEMAND = (
    "Total demand in volume is not in the database: 16.1 stores municipal demand per capita and agricultural demand "
    "per hectare (no population or irrigated area to multiply by); only industrial demand is a volume"
)
EVERY = "A sum needs every district of the scope"


class IwrmService(DistrictModule):
    MODULE = MODULE

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    # ---------------------------------------------------------------- data

    async def year(self) -> pd.Timestamp:
        return await self.latest_month(MAR)

    def areas(self) -> dict[str, float]:
        return self.ctx.state.spatial.district_areas() or {}

    async def recharge_volume(self, year: pd.Timestamp) -> pd.Series:
        """Groundwater recharge per district in Mm³/year = mm/year × km² / 1000."""
        rech = await self.district_values(RECHARGE, year)
        areas = self.areas()
        return pd.Series({k: float(v) * areas[k] / 1000.0 for k, v in rech.dropna().items() if k in areas}, dtype=float)

    async def availability(self, year: pd.Timestamp) -> tuple[pd.Series, pd.Series]:
        """(surface Mm³/year, groundwater Mm³/year) per district."""
        return (await self.district_values(MAR, year)).dropna(), await self.recharge_volume(year)

    def total(self, per: pd.Series) -> float | None:
        return self.aggregate(per, True)

    def in_scope(self, per: pd.Series) -> pd.Series:
        sel = self.ctx.filters.scope
        return per if sel.level == "national" else per[per.index.isin(sel.district_codes)]

    async def years(self) -> list[int]:
        df = await self.data.annual(MAR)
        last = await self.year()
        return sorted({int(pd.Timestamp(d).year) for d in df["date"] if pd.Timestamp(d).year <= last.year})

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        yr = await self.year()
        surface, ground = await self.availability(yr)
        avail = self.total(surface.add(ground, fill_value=0.0)[surface.index.intersection(ground.index)])
        reserve = self.total((await self.district_values(RESERVE, yr)).dropna() / 1e6)
        recycled = self.total((await self.district_values(RETURN_FLOW, yr)).dropna())
        balance = self.total((await self.district_values(BALANCE, yr)).dropna())
        losses = weighted_scalar(await self.district_values(LOSSES, yr), self.ctx.weights())
        kpis = [
            self.kpi(
                "availability",
                "availability",
                "Utilisable Water Availability",
                avail,
                "Mm³/yr",
                yr,
                MAR,
                decimals=0,
                sublabel=f"{yr:%Y}: mean annual runoff + groundwater recharge × district area (16.1), summed",
                reason=EVERY,
            ),
            self.kpi_unavailable("demand", "demand", "Total Water Demand", "Mm³/yr", NO_DEMAND),
            self.kpi(
                "balance",
                "waterBalance",
                "Water Balance (16.1, as stored)",
                balance,
                "Mm³/yr",
                yr,
                BALANCE,
                decimals=0,
                sublabel=f"{yr:%Y}: the table's water-balance column summed over the scope's districts",
                reason=EVERY,
            ),
            self.kpi(
                "reserve",
                "ecologicalReserve",
                "Ecological Reserve Requirement",
                reserve,
                "Mm³/yr",
                yr,
                RESERVE,
                decimals=0,
                sublabel=f"{yr:%Y} (16.1), summed over the scope's districts",
                reason=EVERY,
            ),
            self.kpi(
                "recycled",
                "recycled",
                "Recycled & Return Flows",
                recycled,
                "Mm³/yr",
                yr,
                RETURN_FLOW,
                decimals=0,
                sublabel=f"{yr:%Y} (16.1), summed over the scope's districts",
                reason=EVERY,
            ),
            self.kpi_unavailable("stress", "stressIndex", "Water Stress Index", None, NO_DEMAND),
            self.kpi(
                "losses",
                "nrw",
                "System Water Losses",
                losses,
                "%",
                yr,
                LOSSES,
                decimals=1,
                sublabel=f"{yr:%Y} (16.1)" + ("" if len(self.ctx.weights()) == 1 else ", area-weighted mean"),
            ),
            self.kpi_unavailable(
                "status",
                "programmeProgress",
                "IWRM Sustainability Status",
                None,
                "No programme or status records in the database",
            ),
        ]
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        if metric == "availability":
            years = await self.years()
            idx = [pd.Timestamp(y, 1, 1) for y in years]
            tot, surf, gw, rec = [], [], [], []
            for ts in idx:
                s, g = await self.availability(ts)
                both = s.index.intersection(g.index)
                tot.append(self.total(s[both] + g[both]))
                surf.append(self.total(s))
                gw.append(self.total(g))
                rec.append(self.total((await self.district_values(RETURN_FLOW, ts)).dropna()))
            for key in ("total", "surface", "groundwater", "recycled"):
                self.ctx.explain(f"series.{key}", EVERY)
            self.ctx.explain("series.demand", NO_DEMAND)
            return self.series_set(
                "availability",
                "Water availability (annual, 16.1)",
                [
                    self.series("total", "Total utilisable", pd.Series(tot, index=idx, dtype=float), nd=0, i=0),
                    self.series("surface", "Surface water (MAR)", pd.Series(surf, index=idx, dtype=float), nd=0, i=1),
                    self.series(
                        "groundwater", "Groundwater (recharge)", pd.Series(gw, index=idx, dtype=float), nd=0, i=2
                    ),
                    self.series(
                        "recycled", "Recycled / return flows", pd.Series(rec, index=idx, dtype=float), nd=0, i=3
                    ),
                    c.Series(key="demand", name="Total demand", kind="line", data=[[iso(t.date()), None] for t in idx]),
                ],
                [c.Axis(unit="Mm³/yr", label="Volume")],
                [MAR, RECHARGE, RETURN_FLOW],
                idx[-1] if idx else None,
                note="Mean annual runoff + groundwater recharge × district area, summed over the scope's districts; "
                "demand in volume is not in the database.",
            )
        if metric == "nrw":
            years = await self.years()
            idx = [pd.Timestamp(y, 1, 1) for y in years]
            vals = [weighted_scalar(await self.district_values(LOSSES, ts), self.ctx.weights()) for ts in idx]
            self.ctx.explain("series.nrw", "An area-weighted mean needs at least 95 % of the scope's area")
            return self.series_set(
                "nrw",
                "System water losses (annual, 16.1)",
                [self.series("nrw", "System losses (%)", pd.Series(vals, index=idx, dtype=float), nd=1)],
                [c.Axis(unit="%", label="Non-revenue water", min=0, max=100)],
                [LOSSES],
                idx[-1] if idx else None,
                note="16.1 system water losses, area-weighted over the scope's districts (the table's nearest "
                "measure of non-revenue water).",
            )
        raise self.no_key("timeseries", metric, ["availability", "nrw"], {})

    # ---------------------------------------------------------------- forecast

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns

    async def by_province(self, per: pd.Series, *, decimals: int = 0) -> list[float | None]:
        """Province sums of a district-indexed series (every district of the province needed)."""
        out: list[float | None] = []
        for p in districts.PROVINCES:
            codes = [d.code for d in districts.in_province(p)]
            vals = [per.get(code) for code in codes]
            out.append(rnd(float(sum(vals)), decimals) if all(v is not None and pd.notna(v) for v in vals) else None)
        return out

    async def breakdown(self, metric: str) -> dict[str, Any]:
        yr = await self.year()
        provs = list(districts.PROVINCES.values())
        prov = self.ctx.provenance(yr.date(), self.src(MAR), res="annual")
        prov.pop("asOf")
        prov["aggregation"] = "district"
        if metric == "transfers":
            per = (await self.district_values(IBT, yr)).dropna()
            self.ctx.explain("series.volume", "A province sum needs every district of the province")
            return c.CategorySet(
                id="transfers",
                title=f"Inter-basin transfer volumes by province – {yr:%Y} (16.1)",
                unit="Mm³/yr",
                categories=provs,
                series=[c.CategorySeries(key="volume", name="Transfer volume", values=await self.by_province(per))],
                as_of=iso(yr.date()),
                **{**prov, "source": self.src(IBT)},
            ).dump()
        if metric == "reserve-compliance":
            per = (await self.district_values(RESERVE, yr)).dropna() / 1e6
            self.ctx.explain("series.requirement", "A province sum needs every district of the province")
            self.ctx.explain("series.provided", "Water provided to the Reserve is not in the database")
            self.ctx.explain("series.compliance", "Water provided to the Reserve is not in the database")
            return c.CategorySet(
                id="reserve-compliance",
                title=f"Ecological reserve requirement by province – {yr:%Y} (16.1)",
                unit="Mm³/yr",
                categories=provs,
                series=[
                    c.CategorySeries(
                        key="requirement", name="Ecological reserve requirement", values=await self.by_province(per)
                    ),
                    c.CategorySeries(key="provided", name="Provided (e-flows & quality)", values=[None] * 9),
                    c.CategorySeries(key="compliance", name="Compliance (%)", values=[None] * 9, kind="line"),
                ],
                as_of=iso(yr.date()),
                **{**prov, "source": self.src(RESERVE)},
            ).dump()
        if metric == "recycled":
            per = (await self.district_values(RETURN_FLOW, yr)).dropna()
            self.ctx.explain("series.return", "A province sum needs every district of the province")
            self.ctx.explain("series.recycled", "Recycled-water use is not stored separately from return flows")
            return c.CategorySet(
                id="recycled",
                title=f"Return / recycled flows by province – {yr:%Y} (16.1)",
                unit="Mm³/yr",
                categories=provs,
                series=[
                    c.CategorySeries(key="recycled", name="Recycled water use", values=[None] * 9),
                    c.CategorySeries(key="return", name="Return flows", values=await self.by_province(per)),
                ],
                as_of=iso(yr.date()),
                **{**prov, "source": self.src(RETURN_FLOW)},
            ).dump()
        raise self.no_key(
            "breakdown",
            metric,
            ["transfers", "reserve-compliance", "recycled"],
            {"waterfall": NO_DEMAND, "sector": NO_DEMAND},
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        yr = await self.year()
        if metric == "wmas":
            surface, ground = await self.availability(yr)
            both = surface.index.intersection(ground.index)
            avail = surface[both] + ground[both]
            rows = []
            wma_districts = self.ctx.state.spatial.wma_districts()
            for wma in self.ctx.state.spatial.wmas:
                frac = sum_fractions(parse_scope(wma=wma.id, wma_districts=wma_districts), self.ctx.state.spatial)
                vals = [avail.get(code) for code in frac]
                y = (
                    sum(float(v) * f for v, f in zip(vals, frac.values(), strict=True))
                    if frac and all(v is not None for v in vals)
                    else None
                )
                rows.append(
                    {"id": wma.id, "wma": wma.name, "yield": rnd(y, 0), "demand": None, "balance": None, "wsi": None}
                )
            for key in ("demand", "balance", "wsi"):
                self.ctx.explain(f"rows.{key}", NO_DEMAND)
            self.ctx.explain("rows.yield", "A WMA sum needs every district overlapping the WMA")
            prov = self.ctx.provenance(yr.date(), self.src(MAR, RECHARGE), res="annual")
            prov.pop("asOf")
            prov["aggregation"] = "district-overlap"
            return c.TableSet(
                id="wmas",
                title=f"Water availability by water management area – {yr:%Y} (16.1, district overlap)",
                columns=[
                    c.Column(key="wma", label="Water Management Area", type="text"),
                    c.Column(
                        key="yield", label="Runoff + recharge", type="number", unit="Mm³/yr", decimals=0, align="right"
                    ),
                    c.Column(key="demand", label="Demand", type="number", unit="Mm³/yr", decimals=0, align="right"),
                    c.Column(key="balance", label="Balance", type="number", unit="Mm³/yr", decimals=0, align="right"),
                    c.Column(key="wsi", label="WSI", type="number", decimals=2, align="right"),
                ],
                rows=rows,
                row_key="id",
                as_of=iso(yr.date()),
                **prov,
            ).dump()
        if metric == "summary":
            return await WaterQuantityService(self.ctx).table("summary", limit)
        raise self.no_key(
            "table",
            metric,
            ["wmas", "summary"],
            {"catchments": "Catchment yields, demands and reserves are not in the database (district data only)"},
        )

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "availability":
            return await self.district_map(
                layer, "Mean annual runoff by district (16.1)", MAR, "runoff", nd=0, unit="Mm³/yr"
            )
        if layer == "transfers":
            return await self.district_map(
                layer, "Inter-basin transfer volumes by district (16.1)", IBT, "runoff", nd=0, unit="Mm³/yr"
            )
        if layer == "swai":
            return await self.district_map(
                layer, "Surface water availability index (16.1)", SWAI, "index01", nd=2, unit="—"
            )
        raise self.no_key(
            "map",
            layer,
            ["availability", "transfers", "swai"],
            {
                "stress": NO_DEMAND,
                "wma-stress": NO_DEMAND,
                "catchment-stress": "No catchment data in the database (district data only)",
            },
        )


async def prewarm(data: Any) -> None:
    for var in (MAR, RECHARGE, IBT, RESERVE, RETURN_FLOW, LOSSES):
        await data.annual(var)
    for var, _ in SCHEMES.values():
        await data.monthly(var)
