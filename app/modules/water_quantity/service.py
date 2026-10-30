"""Water Quantity Assessment (contract module `water-quantity`, spec §17; DB module 17: `17_1`, `17_2`; registered
2026-10-29, DECISIONS D42).

- `17_1` holds monthly inter-basin transfer rates (m³/day) per district for the 16 districts of the Vaal / Orange /
  Thukela transfer systems: Katse → Vaal, Tugela–Vaal, Orange River system, Thukela–Vaal emergency / regular, the
  transfer losses, pumping energy, donor-basin storage and the receiving system's deficit. Monthly volumes = rate ×
  days in the month; sums run over the districts the table holds (its 16, never all 52).
- `17_2` is a national monthly water balance without a district breakdown. Most of its columns are implausible in
  their unit (D42) and withheld; the surface storage (Mm³, consistent with the national dam capacity) and the actual
  evapotranspiration are served, for South Africa only.
- Groundwater recharge and abstraction come from the groundwater-reserve tables (11.2 / 11.3, annual); surface
  availability from the 16.1 mean annual runoff per district (annual).

Not in the database: utilisable yield, licensed allocations, sector demand in volume (16.1 stores municipal demand
per capita and agricultural demand per hectare), catchment yields and a water-stress index — those panels stay 501.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from app.core import districts
from app.core.errors import DataUnavailable
from app.core.palettes import SERIES
from app.core.provenance import iso
from app.core.stats import rnd
from app.modules.common import Ctx
from app.modules.district import DistrictModule
from app.modules.groundwater_reserve.service import ABSTRACTION, DIRECT, GroundwaterReserveService
from app.registry.registry import NATIONAL_CODE
from app.schemas import contract as c

MODULE = "water-quantity"
SCHEMES: dict[str, tuple[str, str]] = {  # key → (variable, label)
    "katse": ("wqa_katse_to_vaal_transfer", "Katse → Vaal"),
    "tugela": ("wqa_tugela_vaal_transfer", "Tugela–Vaal"),
    "orange": ("wqa_orange_river_system_transfer", "Orange River system"),
    "thukela": ("wqa_thukela_vaal_emergency_transfer", "Thukela–Vaal emergency / regular"),
}
LOSS = "wqa_transfer_loss"
ENERGY = "wqa_transfer_pumping_energy"
DONOR = "wqa_donor_basin_storage_fsc"
DEFICIT = "wqa_receiving_demand_deficit"
STORAGE = "nwb_surface_storage"
AET = "nwb_aet"
MAR = "wrm_mean_annual_runoff_mar_million_m3_yr"
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
NATIONAL_ONLY = "17_2 is a national water balance without a district breakdown: available for South Africa only"
NO_YIELD = (
    "Utilisable yield, allocations and sector demand volumes are not in the database (17_2's balance columns are "
)
"implausible in their unit and withheld, DECISIONS D42)"
TRANSFER_NOTE = "17_1: monthly transfer rate × days in the month, summed over the districts the table holds (the 16 "
"districts of the transfer systems)"


class WaterQuantityService(DistrictModule):
    MODULE = MODULE

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)
        self.gw = GroundwaterReserveService(ctx)

    # ---------------------------------------------------------------- data

    async def national_series(self, var: str) -> pd.Series:
        """A 17_2 column per month (complete months only), indexed by month start."""
        df = await self.monthly_frame(var)
        last = await self.latest_month(var)
        df = df[(df["district"] == NATIONAL_CODE) & (df["date"] <= last)]
        return df.set_index("date")["value"].dropna().sort_index()

    def require_national(self) -> None:
        if self.ctx.filters.scope.level != "national":
            raise DataUnavailable(NATIONAL_ONLY, missing=["17_2 district breakdown"])

    async def transfer_frame(self, var: str) -> pd.DataFrame:
        """district, date, volume (Mm³ in the month) of a 17_1 rate column, districts of the scope only."""
        df = await self.monthly_frame(var)
        last = await self.latest_month(var)
        sel = self.ctx.filters.scope
        df = df[df["date"] <= last]
        if sel.level != "national":
            df = df[df["district"].isin(sel.district_codes)]
        if df.empty:
            return pd.DataFrame(
                {
                    "district": pd.Series(dtype=str),
                    "date": pd.Series(dtype="datetime64[ns]"),
                    "volume": pd.Series(dtype=float),
                }
            )
        days = df["date"].map(lambda t: pd.Timestamp(t).days_in_month).astype(float)
        return df.assign(volume=df["value"] * days / 1e6)[["district", "date", "volume"]]

    async def transfer_window(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        last = await self.latest_month(SCHEMES["katse"][0])
        return last - pd.DateOffset(months=11), last

    async def transfer_totals(self) -> tuple[dict[str, float | None], pd.Timestamp, pd.Timestamp]:
        """Mm³ over the last 12 complete months per scheme (and the losses), summed over the scope's districts."""
        start, end = await self.transfer_window()
        out: dict[str, float | None] = {}
        for key, (var, _) in [*SCHEMES.items(), ("loss", (LOSS, "Transfer losses"))]:
            df = await self.transfer_frame(var)
            win = df[(df["date"] >= start) & (df["date"] <= end)]
            out[key] = float(win["volume"].sum()) if not win.empty else None
        return out, start, end

    async def transfers_by_district(self) -> tuple[pd.Series, pd.Timestamp, pd.Timestamp]:
        """Mm³ over the last 12 complete months per district, the four schemes together."""
        start, end = await self.transfer_window()
        parts = []
        for var, _ in SCHEMES.values():
            df = await self.transfer_frame(var)
            parts.append(df[(df["date"] >= start) & (df["date"] <= end)])
        allv = pd.concat(parts) if parts else pd.DataFrame(columns=["district", "date", "volume"])
        return allv.groupby("district")["volume"].sum(), start, end

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        yr = await self.gw.year()
        rech = self.gw.sum_scope(await self.gw.recharge_by_district(yr))
        abst = self.gw.sum_scope(await self.gw.abstraction_by_district(yr))
        mar_year = await self.latest_month(MAR)
        mar = self.gw.sum_scope(await self.district_values(MAR, mar_year))
        totals, start, end = await self.transfer_totals()
        schemes = [v for k, v in totals.items() if k != "loss" and v is not None]
        transfers = sum(schemes) if schemes else None
        kpis = [
            self.kpi_unavailable("availability", "availability", "Total Water Availability", "Mm³/yr", NO_YIELD),
            self.kpi(
                "surface",
                "availability",
                "Surface Water Availability",
                mar,
                "Mm³/yr",
                mar_year,
                MAR,
                decimals=0,
                sublabel=f"{mar_year:%Y}: mean annual runoff (16.1), sum over the scope's districts",
                reason="A sum needs every district of the scope",
            ),
            self.kpi(
                "groundwater",
                "availability",
                "Groundwater Availability",
                rech,
                "Mm³/yr",
                yr,
                DIRECT,
                decimals=0,
                sublabel=f"{yr:%Y}: total recharge (11.2), sum over the scope's districts",
                reason="A sum needs every district of the scope",
            ),
            self.kpi(
                "abstraction",
                "abstraction",
                "Total Abstraction",
                abst,
                "Mm³/yr",
                yr,
                ABSTRACTION,
                decimals=0,
                sublabel=f"{yr:%Y}: groundwater abstraction (11.3); surface abstraction is not in the database",
                reason="A sum needs every district of the scope",
            ),
            self.kpi_unavailable(
                "allocation",
                "allocationUse",
                "Licensed Allocation Utilised",
                "%",
                "No water-use licences or allocations in the database",
            ),
            self.kpi(
                "transfers",
                "transfers",
                "Inter-basin Transfers",
                transfers,
                "Mm³/yr",
                end,
                SCHEMES["katse"][0],
                decimals=0,
                sublabel=f"{start:%b %Y} – {end:%b %Y}: four schemes (17.1)",
                reason="No transfer-system district in this scope (17.1 covers 16 districts)",
            ),
            self.kpi_unavailable("demand", "demand", "Total Water Demand", "Mm³/yr", NO_YIELD),
            self.kpi_unavailable("stress", "stressIndex", "Water Security Index", None, NO_YIELD),
        ]
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        if metric == "storage":
            self.require_national()
            s = await self.national_series(STORAGE)
            start, end = await self.window(STORAGE)
            s = s[(s.index >= start) & (s.index <= end)]
            self.ctx.explain("series.pct", "The full supply capacity is not in the database")
            return self.series_set(
                "storage",
                "Surface storage (national)",
                [
                    self.series("storage", "Total storage", s, nd=0),
                    c.Series(
                        key="pct",
                        name="% full supply capacity",
                        kind="line",
                        axis=1,
                        color=SERIES[1],
                        data=[[iso(pd.Timestamp(t).date()), None] for t in s.index],
                    ),
                ],
                [c.Axis(unit="Mm³", label="Storage"), c.Axis(unit="%", label="% FSC")],
                [STORAGE],
                s.index.max() if not s.empty else None,
                note="17_2 surface storage, one national value per month (no district breakdown).",
            )
        if metric == "groundwater":
            years = await self.gw_years()
            rech, abst = [], []
            for y in years:
                ts = pd.Timestamp(y, 1, 1)
                rech.append(self.gw.sum_scope(await self.gw.recharge_by_district(ts)))
                abst.append(self.gw.sum_scope(await self.gw.abstraction_by_district(ts)))
            idx = [pd.Timestamp(y, 1, 1) for y in years]
            r = pd.Series(rech, index=idx, dtype=float)
            a = pd.Series(abst, index=idx, dtype=float)
            self.ctx.explain("series.recharge", "A sum needs every district of the scope")
            self.ctx.explain("series.abstraction", "A sum needs every district of the scope")
            self.ctx.explain("series.net", "A sum needs every district of the scope")
            return self.series_set(
                "groundwater",
                "Groundwater recharge and abstraction (annual)",
                [
                    self.series("recharge", "Recharge", r, nd=0, i=0),
                    self.series("abstraction", "Abstraction", a, nd=0, i=1),
                    self.series("net", "Net recharge", r - a, nd=0, i=2),
                ],
                [c.Axis(unit="Mm³/yr", label="Volume")],
                [DIRECT, ABSTRACTION],
                idx[-1] if idx else None,
                note="Annual values (11.2 recharge = direct × district area + indirect; 11.3 abstraction), summed over "
                "the scope's districts; the contract's monthly volumes are not in the database.",
            )
        raise self.no_key(
            "timeseries",
            metric,
            ["storage", "groundwater"],
            {"surface": "Surface inflow and outflow volumes are not in the database (17_2's runoff is withheld, D42)"},
        )

    async def gw_years(self) -> list[int]:
        df = await self.data.annual(DIRECT)
        last = await self.gw.year()
        years = sorted({int(pd.Timestamp(d).year) for d in df["date"]} & set(range(2000, last.year + 1)))
        return years

    # ---------------------------------------------------------------- forecast

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        if metric == "transfers":
            totals, start, end = await self.transfer_totals()
            cats = [*(label for _, label in SCHEMES.values()), "Transfer losses (seepage, evaporation)"]
            vals = [rnd(totals.get(k), 1) for k in [*SCHEMES, "loss"]]
            if all(v is None for v in vals):
                self.ctx.explain(
                    "series.volume", "No transfer-system district in this scope (17.1 covers 16 districts)"
                )
            prov = self.ctx.provenance(end.date(), self.src(SCHEMES["katse"][0], LOSS), res="monthly")
            prov.pop("asOf")
            prov["aggregation"] = "district"
            return c.CategorySet(
                id="transfers",
                title=f"Inter-basin transfers, {start:%b %Y} – {end:%b %Y}",
                unit="Mm³/yr",
                categories=cats,
                series=[c.CategorySeries(key="volume", name="Transfer volume (last 12 months)", values=vals)],
                as_of=iso(end.date()),
                **prov,
            ).dump()
        raise self.no_key(
            "breakdown",
            metric,
            ["transfers"],
            {
                "waterfall": NO_YIELD,
                "sector": "Sector demand in volume is not in the database (16.1 stores municipal demand per capita and "
                "agricultural demand per hectare; 17_2's sector demands are withheld, D42)",
                "allocation": "No water-use licences or allocations in the database",
            },
        )

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric != "summary":
            raise self.no_key(
                "table",
                metric,
                ["summary"],
                {"catchments": "Catchment yields, demands and reserves are not in the database (district data only)"},
            )
        rows: list[dict[str, Any]] = []
        sources: set[str] = set()
        dates: list[dt.date] = []

        def add(rid: str, indicator: str, value: float | None, unit: str, when: pd.Timestamp | None, var: str) -> None:
            rows.append(
                {
                    "id": rid,
                    "indicator": indicator,
                    "value": rnd(value, 0),
                    "unit": unit,
                    "asOf": iso(when.date()) if when is not None else None,
                }
            )
            if value is None:
                self.ctx.explain(f"rows.{rid}", "Not available for this scope")
            sources.update(self.src(var))
            if when is not None:
                dates.append(when.date())

        if self.ctx.filters.scope.level == "national":
            st = await self.national_series(STORAGE)
            add(
                "storage",
                "Surface storage (national, latest month)",
                float(st.iloc[-1]) if not st.empty else None,
                "Mm³",
                st.index.max() if not st.empty else None,
                STORAGE,
            )
            aet = await self.national_series(AET)
            add(
                "aet",
                "Actual evapotranspiration (national, last 12 months)",
                float(aet.iloc[-12:].sum()) if len(aet) >= 12 else None,
                "Mm³",
                aet.index.max() if not aet.empty else None,
                AET,
            )
        totals, start, end = await self.transfer_totals()
        schemes = [v for k, v in totals.items() if k != "loss" and v is not None]
        add(
            "transfers",
            f"Inter-basin transfers ({start:%b %Y} – {end:%b %Y})",
            sum(schemes) if schemes else None,
            "Mm³",
            end,
            SCHEMES["katse"][0],
        )
        add("losses", "Transfer losses (same 12 months)", totals.get("loss"), "Mm³", end, LOSS)
        yr = await self.gw.year()
        add(
            "recharge",
            f"Groundwater recharge ({yr:%Y})",
            self.gw.sum_scope(await self.gw.recharge_by_district(yr)),
            "Mm³/yr",
            yr,
            DIRECT,
        )
        add(
            "abstraction",
            f"Groundwater abstraction ({yr:%Y})",
            self.gw.sum_scope(await self.gw.abstraction_by_district(yr)),
            "Mm³/yr",
            yr,
            ABSTRACTION,
        )
        mar_year = await self.latest_month(MAR)
        add(
            "mar",
            f"Mean annual runoff ({mar_year:%Y}, 16.1)",
            self.gw.sum_scope(await self.district_values(MAR, mar_year)),
            "Mm³/yr",
            mar_year,
            MAR,
        )
        prov = self.ctx.provenance(max(dates) if dates else None, sorted(sources), res="monthly")
        prov.pop("asOf")
        return c.TableSet(
            id="summary",
            title="Water quantity: what the database holds",
            columns=[
                c.Column(key="indicator", label="Indicator", type="text"),
                c.Column(key="value", label="Value", type="number", decimals=0, align="right"),
                c.Column(key="unit", label="Unit", type="text"),
                c.Column(key="asOf", label="As of", type="date"),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(max(dates)) if dates else None,
            **prov,
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "availability":
            return await self.district_map(
                layer, "Mean annual runoff by district (16.1)", MAR, "runoff", nd=0, unit="Mm³/yr"
            )
        if layer == "transfers":
            per, start, end = await self.transfers_by_district()
            sel = self.ctx.filters.scope
            keep = set(sel.district_codes)
            out = {str(k): rnd(float(v), 1) for k, v in per.items() if k in keep}
            if not out:
                self.ctx.explain("values", "No transfer-system district in this scope (17.1 covers 16 districts)")
            prov = self.ctx.provenance(end.date(), self.src(SCHEMES["katse"][0]), res="monthly")
            prov["aggregation"] = "district"
            return c.MapLayer(
                id=layer,
                title=f"Inter-basin transfers by district, {start:%b %Y} – {end:%b %Y} (Mm³)",
                kind="choropleth",
                level="municipality",
                scale="runoff",
                unit="Mm³/yr",
                date=iso(end.date()) or "",
                values=out,
                geometry="districts",
                **prov,
            ).dump()
        raise self.no_key(
            "map",
            layer,
            ["availability", "transfers"],
            {
                "stress": NO_YIELD,
                "wma-stress": NO_YIELD,
                "catchment-stress": "No catchment data in the database (district data only)",
                "demand-density": "Municipal demand in volume is not in the database (16.1 stores litres per capita "
                "per day without population)",
            },
        )

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.district_entities("district")

    async def entity(self, code: str) -> dict[str, Any]:
        d = self.district(code)
        per, start, end = await self.transfers_by_district()
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            c.EntityField(
                label=f"Inter-basin transfers ({start:%b %Y} – {end:%b %Y}, Mm³)", value=rnd(per.get(d.code), 1)
            ),
            await self.field("Mean annual runoff (16.1)", MAR, d.code, nd=0),
            await self.field("Donor basin storage (17.1)", DONOR, d.code, nd=1),
            await self.field("Receiving system deficit (17.1)", DEFICIT, d.code, nd=0),
        ]
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type="district",
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code} · district water-quantity record (16.1, 17.1)",
            title=d.official_name,
            fields=fields,
            image="municipality",
            as_of=iso(end.date()),
            source=self.src(MAR, SCHEMES["katse"][0]),
        ).dump()


async def prewarm(data: Any) -> None:
    for var, _ in SCHEMES.values():
        await data.monthly(var)
    await data.monthly(STORAGE)
    await data.annual(MAR)
