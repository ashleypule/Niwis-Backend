"""Groundwater Reserve Assessment (contract module `groundwater-reserve`, spec §11; DB module 9: `11_1`, `11.2`,
`11.3`).

All three tables hold one record per district (no aquifer units or codes; DECISIONS D12):

- static aquifer properties from `11_1` (consistent with T = K × b; the `10_1` aquifer constants are withheld, D9),
  depth to water from its long-format variable;
- recharge from `11.2`: total recharge = direct recharge (mm/year × district area) + indirect recharge (m³/year) — the
  spec's two recharge components (§11.2); identical triplicate rows are read once (D18); its baseflow is withheld;
- abstraction, exploitation index (stored in %, classified as a ratio with spec §11.3 classes from thresholds.json),
  borehole yield, depletion rate and recharge sensitivity from `11.3`;
- static reserve (Mm³) and dynamic reserve (Mm³/year) summed over the scope.

Not in the database: aquifer units, the ecological reserve and hence the additional allowable abstraction, a
sustainable yield in Mm³/year (11.3 stores borehole yield in m³/h per borehole).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from app.core import districts
from app.core.palettes import NORMAL, SERIES
from app.core.provenance import iso
from app.core.stats import rnd, sens_slope
from app.core.thresholds import exploitation_class, thresholds
from app.modules.common import Ctx
from app.modules.district import DistrictModule
from app.schemas import contract as c

MODULE = "groundwater-reserve"
DEPTH = "hg_depth_to_water_table"
STATIC = "hg_static_reserve_volume"  # Mm³
DYNAMIC = "hg_dynamic_renewable_reserve"  # Mm³/year
DIRECT = "rd_direct_recharge"  # mm/year
INDIRECT = "rd_indirect_recharge"  # m³/year
ABSTRACTION = "as_total_abstraction"  # m³/year
EI = "as_exploitation_index"  # %
HISTORY_START = dt.date(2000, 1, 1)
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
ENTITY_DATASETS = {("timeseries", "recharge-abstraction"), ("breakdown", "components")}
NO_ECO = "The groundwater ecological reserve is not in the database (15.1 holds a surface-water reserve flow)"


class GroundwaterReserveService(DistrictModule):
    MODULE = MODULE

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    def use_entity(self, kind: str, key: str) -> None:
        if (kind, key) in ENTITY_DATASETS and self.ctx.filters.entity_id:
            self.ctx = self.ctx.for_entity()

    def entity_title(self, title: str) -> str:
        return f"{title} — {self.ctx.filters.scope.name}" if self.ctx.filters.entity_id else title

    # ---------------------------------------------------------------- data

    async def year(self) -> pd.Timestamp:
        """Latest complete year with recharge and abstraction."""
        return min(await self.latest_month(DIRECT), await self.latest_month(ABSTRACTION), await self.latest_month(EI))

    async def recharge_by_district(self, year: pd.Timestamp) -> pd.Series:
        """Total recharge per district (Mm³/year) = direct (mm/year × km² / 1000) + indirect (m³/year / 10⁶)."""
        areas = self.ctx.state.spatial.district_areas() or {}
        direct = await self.district_values(DIRECT, year)
        indirect = await self.district_values(INDIRECT, year)
        out = {}
        for code in set(direct.index) & set(indirect.index):
            if code in areas and pd.notna(direct[code]) and pd.notna(indirect[code]):
                out[code] = float(direct[code]) * areas[code] / 1000.0 + float(indirect[code]) / 1e6
        return pd.Series(out, dtype=float)

    async def abstraction_by_district(self, year: pd.Timestamp) -> pd.Series:
        return (await self.district_values(ABSTRACTION, year)) / 1e6

    async def ei_by_district(self, year: pd.Timestamp) -> pd.Series:
        return await self.district_values(EI, year)

    def sum_scope(self, per: pd.Series) -> float | None:
        return self.aggregate(per, True)

    def in_scope(self, per: pd.Series) -> pd.Series:
        sel = self.ctx.filters.scope
        return per if sel.level == "national" else per[per.index.isin(sel.district_codes)]

    # ---------------------------------------------------------------- summary

    async def summary(self) -> dict[str, Any]:
        yr = await self.year()
        rech = await self.recharge_by_district(yr)
        abst = await self.abstraction_by_district(yr)
        ei = await self.ei_by_district(yr)
        sm = await self.latest_month(STATIC)
        mine = self.in_scope(ei.dropna())
        risk = float(
            sum(1 for v in mine if exploitation_class(float(v) / 100) in ("High", "Very High", "Over-exploited"))
        )
        n = len(self.in_scope(rech))
        kpis = [
            self.kpi(
                "units",
                "boreholes",
                "Districts with Aquifer Records",
                float(n) if n else None,
                "districts",
                yr,
                DIRECT,
                decimals=0,
                sublabel="no aquifer units in the database: one record per district",
            ),
            self.kpi(
                "recharge",
                "rechargeVolume",
                "Total Recharge",
                self.sum_scope(rech),
                "Mm³/yr",
                yr,
                DIRECT,
                decimals=0,
                sublabel=f"{yr:%Y}: direct (× area) + indirect recharge (11.2)",
                reason="A sum needs every district of the scope",
            ),
            self.kpi(
                "abstraction",
                "abstraction",
                "Total Abstraction",
                self.sum_scope(abst),
                "Mm³/yr",
                yr,
                ABSTRACTION,
                decimals=0,
                sublabel=f"{yr:%Y} (11.3)",
                reason="A sum needs every district of the scope",
            ),
            self.kpi(
                "dynamic",
                "dynamicReserve",
                "Dynamic Reserve",
                self.sum_scope(await self.district_values(DYNAMIC, await self.latest_month(DYNAMIC))),
                "Mm³/yr",
                await self.latest_month(DYNAMIC),
                DYNAMIC,
                decimals=0,
                sublabel="renewable reserve (11_1)",
                reason="A sum needs every district of the scope",
            ),
            self.kpi(
                "static",
                "staticReserve",
                "Static Reserve",
                self.sum_scope(await self.district_values(STATIC, sm)),
                "Mm³",
                sm,
                STATIC,
                decimals=0,
                sublabel="volume in storage (11_1)",
                reason="A sum needs every district of the scope",
            ),
        ]
        ei_v = self.aggregate(ei, False)
        cls = exploitation_class(ei_v / 100) if ei_v is not None else None
        kpis.append(
            self.kpi(
                "ei",
                "exploitationIndex",
                "Exploitation Index",
                ei_v,
                "%",
                yr,
                EI,
                decimals=1,
                sublabel=f"{yr:%Y}, abstraction / recharge"
                + ("" if len(self.ctx.weights()) == 1 else " (area-weighted mean)")
                + (f" · {cls}" if cls else ""),
                status=c.Status(scale="exploitation", id=cls, label=cls) if cls else None,
            )
        )
        kpis.append(
            self.kpi(
                "risk",
                "aquifersAtRisk",
                "Districts at Risk",
                risk if len(mine) else None,
                "districts",
                yr,
                EI,
                decimals=0,
                sublabel=f"of {len(mine)}: exploitation index ≥ 0.5 (High or worse, spec §11.3)",
            )
        )
        return self.kpi_set(kpis)

    # ---------------------------------------------------------------- timeseries

    async def timeseries(self, metric: str) -> dict[str, Any]:
        self.use_entity("timeseries", metric)
        if metric == "level-trend":
            return await self.ts_level_trend()
        if metric == "recharge-abstraction":
            return await self.ts_recharge_abstraction()
        raise self.no_key(
            "timeseries",
            metric,
            ["level-trend", "recharge-abstraction"],
            {
                "yield-ei": "A sustainable yield in Mm³/year is not in the database (11.3 stores borehole yield in "
                "m³/h per borehole)"
            },
        )

    async def ts_level_trend(self) -> dict[str, Any]:
        start, end = await self.window(DEPTH)
        s = await self.scope_series(DEPTH)
        last = await self.latest_month(DEPTH)
        y1 = int(last.year) if last.month == 12 else int(last.year) - 1
        ref = s[(s.index.year >= HISTORY_START.year) & (s.index.year <= y1)]
        win = s[(s.index >= start) & (s.index <= end)]
        annual = ref.groupby(ref.index.year).mean()
        t = sens_slope(annual.tolist())
        if t is None:
            self.ctx.explain("series.trend", "Too few complete years for a trend (8 needed)")
        # trend line evaluated per month from the annual Sen's slope (per year) and its intercept
        if t is not None:
            base_year = int(annual.index[0])
            trend = pd.Series(
                {m: t.intercept + t.slope * (m.year - base_year + (m.month - 0.5) / 12) for m in win.index}
            )
        else:
            trend = pd.Series(np.nan, index=win.index)
        return self.series_set(
            "level-trend",
            "Depth to water (11_1)",
            [
                self.series(
                    "observed", "Observed level", win, nd=2, i=0, source=self.ctx.col_source(DEPTH).get("source")
                ),
                c.Series(
                    key="lt",
                    name=f"Long-term mean ({HISTORY_START.year}–{y1})",
                    kind="line",
                    color=NORMAL,
                    dash=True,
                    data=[[iso(m.date()), rnd(ref.mean() if len(ref) else None, 2)] for m in win.index],
                ),
                self.series(
                    "trend",
                    f"Trend (Sen's slope{f', {t.slope * 10:+.2f} m/decade' if t else ''})",
                    trend,
                    nd=2,
                    i=3,
                ),
            ],
            [c.Axis(unit="m bgl", label="Depth to water", inverse=True)],
            [DEPTH],
            end,
            note="Monthly depth to water as stored in 11_1 ("
            + ("district value" if len(self.ctx.weights()) == 1 else "area-weighted district mean")
            + f"); trend over the annual means {HISTORY_START.year}–{y1}.",
        )

    async def ts_recharge_abstraction(self) -> dict[str, Any]:
        last = await self.year()
        years = []
        rech, abst = {}, {}
        dfr = await self.monthly_frame(DIRECT)
        for y in sorted({int(t.year) for t in dfr["date"]}):
            if y > last.year:
                continue
            ts = pd.Timestamp(y, 1, 1)
            r = self.sum_scope(await self.recharge_by_district(ts))
            a = self.sum_scope(await self.abstraction_by_district(ts))
            if r is not None and a is not None:
                years.append(y)
                rech[y], abst[y] = r, a
        if not years:
            for k in ("recharge", "abstraction"):
                self.ctx.explain(f"series.{k}", "A sum needs every district of the scope")
        return self.series_set(
            "recharge-abstraction",
            self.entity_title("Recharge and abstraction"),
            [
                c.Series(
                    key="recharge",
                    name="Recharge",
                    kind="bar",
                    color=SERIES[0],
                    data=[[str(y), rnd(rech[y], 1)] for y in years],
                ),
                c.Series(
                    key="abstraction",
                    name="Abstraction",
                    kind="bar",
                    color=SERIES[3],
                    data=[[str(y), rnd(abst[y], 1)] for y in years],
                ),
            ],
            [c.Axis(unit="Mm³/yr", label="Volume")],
            [DIRECT, ABSTRACTION],
            last,
            x_type="category",
            note="Recharge = direct recharge × district area + indirect recharge (11.2); abstraction = total "
            "abstraction (11.3); sums over the scope's districts (every district needed).",
        )

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise self.no_key("forecast", metric, [], {})

    # ---------------------------------------------------------------- breakdowns

    async def breakdown(self, metric: str) -> dict[str, Any]:
        self.use_entity("breakdown", metric)
        yr = await self.year()
        prov = self.ctx.provenance(yr.date(), self.src(EI, ABSTRACTION, DYNAMIC), res="annual")
        prov.pop("asOf")
        if metric == "ei-classes":
            ei = self.in_scope((await self.ei_by_district(yr)).dropna())
            order = [x["id"] for x in thresholds()["exploitation"]["classes"]] + [
                thresholds()["exploitation"]["otherwise"]
            ]
            counts = [float(sum(1 for v in ei if exploitation_class(float(v) / 100) == k)) for k in order]
            prov["aggregation"] = "district"
            return c.CategorySet(
                id="ei-classes",
                title=f"Districts by exploitation class – {yr:%Y}",
                unit="districts",
                categories=order,
                colors=["#1F6FD1", "#74B4E0", "#B8E0A8", "#F6CF3E", "#F28C28", "#B2182B"],
                series=[c.CategorySeries(key="units", name="Districts", values=counts)],
                as_of=iso(yr.date()),
                **prov,
            ).dump()
        if metric == "components":
            dyn = self.sum_scope(await self.district_values(DYNAMIC, await self.latest_month(DYNAMIC)))
            ab = self.sum_scope(await self.abstraction_by_district(yr))
            self.ctx.explain("series.volume", "A sum needs every district of the scope. " + NO_ECO)
            return c.CategorySet(
                id="components",
                title=self.entity_title(f"Groundwater reserve components – {yr:%Y}"),
                unit="Mm³/yr",
                categories=[
                    "Dynamic reserve",
                    "Ecological reserve (not in the database)",
                    "Current abstraction",
                    "Additional allowable abstraction (needs the ecological reserve)",
                ],
                series=[c.CategorySeries(key="volume", name="Volume", values=[rnd(dyn, 0), None, rnd(ab, 0), None])],
                as_of=iso(yr.date()),
                **prov,
            ).dump()
        raise self.no_key("breakdown", metric, ["ei-classes", "components"], {})

    # ---------------------------------------------------------------- tables

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        if metric == "aquifers":
            return await self.tbl_aquifers(limit)
        if metric == "lowest":
            from app.modules.groundwater.service import GroundwaterService

            return await GroundwaterService(self.ctx).table("lowest", limit)  # the groundwater page's level table
        raise self.no_key("table", metric, ["aquifers", "lowest"], {})

    async def tbl_aquifers(self, limit: int | None) -> dict[str, Any]:
        yr = await self.year()
        rech = await self.recharge_by_district(yr)
        abst = await self.abstraction_by_district(yr)
        ei = await self.ei_by_district(yr)
        static = await self.district_values(STATIC, await self.latest_month(STATIC))
        rows = []
        for code in self.in_scope(ei.dropna()).sort_values(ascending=False).index:
            d = districts.by_code(str(code))
            if d is None:
                continue
            v = float(ei[code])
            rows.append(
                {
                    "id": d.code,
                    "aquifer": d.official_name,
                    "recharge": rnd(rech.get(code), 1),
                    "abstraction": rnd(abst.get(code), 1),
                    "ei": rnd(v / 100, 3),
                    "static": rnd(static.get(code), 0),
                    "status": exploitation_class(v / 100),
                }
            )
        prov = self.ctx.provenance(yr.date(), self.src(EI, DIRECT, ABSTRACTION, STATIC), res="annual")
        prov.pop("asOf")
        prov["aggregation"] = "district"
        return c.TableSet(
            id="aquifers",
            title=f"Groundwater balance by district – {yr:%Y}",
            columns=[
                c.Column(key="aquifer", label="District", type="text"),
                c.Column(key="recharge", label="Recharge", type="number", unit="Mm³/yr", decimals=1, align="right"),
                c.Column(
                    key="abstraction", label="Abstraction", type="number", unit="Mm³/yr", decimals=1, align="right"
                ),
                c.Column(key="ei", label="EI", type="number", decimals=3, align="right"),
                c.Column(key="static", label="Static reserve", type="number", unit="Mm³", decimals=0, align="right"),
                c.Column(key="status", label="Status", type="class", scale="exploitation"),
            ],
            rows=rows[: limit or 20],
            row_key="id",
            link_to="entity",
            total=len(rows),
            as_of=iso(yr.date()),
            **prov,
        ).dump()

    # ---------------------------------------------------------------- maps

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "ei":
            return await self.district_map(
                layer,
                "Exploitation index (abstraction / recharge)",
                EI,
                "exploitation",
                nd=3,
                transform=lambda v: v / 100,
                unit="—",
            )
        if layer == "recharge":
            return await self.district_map(layer, "Direct recharge", DIRECT, "runoff", nd=0, unit="mm")
        if layer == "gw-level":
            return await self.district_map(layer, "Depth to water (11_1)", DEPTH, "gwDepth", nd=1)
        if layer == "sgi":
            from app.modules.groundwater.service import GroundwaterService

            return await GroundwaterService(self.ctx).map("sgi")
        raise self.no_key("map", layer, ["ei", "recharge", "gw-level", "sgi"], {})

    # ---------------------------------------------------------------- entities

    async def entities(self) -> dict[str, Any]:
        return self.district_entities("district")

    async def entity(self, code: str) -> dict[str, Any]:
        d = self.district(code)
        yr = await self.year()
        ei = (await self.ei_by_district(yr)).get(d.code)
        cls = exploitation_class(float(ei) / 100) if ei is not None and pd.notna(ei) else None
        fields = [
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            c.EntityField(label=f"Exploitation index ({yr:%Y})", value=rnd(float(ei) / 100, 3) if cls else None),
            await self.field("Aquifer extent", "hg_aquifer_extent_map_area", d.code, nd=0),
            await self.field("Saturated thickness", "hg_aquifer_saturated_thickness", d.code, nd=1),
            await self.field("Transmissivity", "hg_transmissivity", d.code, nd=1),
            await self.field("Specific yield", "hg_specific_yield", d.code, nd=3),
            await self.field("Static reserve", STATIC, d.code, nd=0),
            await self.field("Dynamic reserve", DYNAMIC, d.code, nd=0),
            await self.field("Direct recharge", DIRECT, d.code, nd=0),
            await self.field("Borehole yield", "as_borehole_yield", d.code, nd=1),
            await self.field("Aquifer depletion rate", "as_aquifer_depletion_rate", d.code, nd=2),
        ]
        return c.EntityDetail(
            id=d.code,
            name=d.official_name,
            type="district",
            province=d.province,
            sub=f"{districts.PROVINCES[d.province]} · MDB {d.code} · district aquifer record (11_1 – 11.3)",
            title=d.official_name,
            fields=fields,
            image="aquifer",
            **({"status": c.Status(scale="exploitation", id=cls, label=cls)} if cls else {}),
            as_of=iso(yr.date()),
            source=self.src(EI, STATIC, DIRECT),
        ).dump()


async def prewarm(data: Any) -> None:
    await data.monthly(DEPTH)
    for var in (STATIC, DYNAMIC, DIRECT, INDIRECT, ABSTRACTION, EI):
        await data.annual(var)
