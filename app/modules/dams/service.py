"""Dam & Reservoir Status (contract module `dams`, spec §8; DB module 6: `8_1_reservoir_operational_master`).

`8_1` names one combined reservoir per district (two Global Dam Watch reservoirs joined, or a named dam such as Gariep
or Vaal next to one), with catchment rainfall, soil moisture, runoff, NDWI, storage, level, inflow, outflow,
evaporation and seepage. Its storage fails the plausibility check (docs/DECISIONS.md D2): 51 of 52 districts hold the
same 251 997 542.57 m³ = 100 % FSC, level 1 200 m and 52.5 km² water area on every day since 2000; runoff / inflow are
one constant per district from 2001; inflow = runoff × a 10 000 km² placeholder area and outflow ≈ inflow. So every
storage, flow and alert panel returns the unavailable convention with that reason, the alert levels of spec §8.2 are
implemented but have no valid storage to classify, and the page serves what is real: the 52 reservoirs as entities
(name, river, province from the table) with their catchment rainfall and soil moisture, and the database view of the
table (catchment rainfall to 31 Aug 2026, soil moisture). No coordinates exist, so there are no point layers (D12).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core import districts
from app.core.errors import NotFound
from app.core.thresholds import storage_alert_level, thresholds
from app.db import ident
from app.modules.common import Ctx
from app.modules.district import NO_POINTS, DistrictModule
from app.schemas import contract as c

MODULE = "dams"
TABLE = "8_1_reservoir_operational_master"
STORAGE = "res_storage_volume_m3"
FSC = "res_pct_fsc"
RAIN = "res_rainfall_mm_day"
SM = "res_soil_moisture_m3_m3"
SELECTORS: dict[tuple[str, str], tuple[Any, ...]] = {}
ENTITY_DATASETS: set[tuple[str, str]] = set()


def days_to_empty(volume_mm3: float, net_drawdown_mm3_per_day: float) -> int | None:
    """Spec §8.2: days until empty at the current net draw-down rate (Mm³/day); None while the dam is filling."""
    if net_drawdown_mm3_per_day <= 0:
        return None
    return round(volume_mm3 / net_drawdown_mm3_per_day)


class DamsService(DistrictModule):
    MODULE = MODULE

    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)

    # ---------------------------------------------------------------- reasons

    def storage_reason(self) -> str:
        return self.reason(STORAGE)

    def flow_reason(self) -> str:
        return self.reason("res_inflow_m3_s")

    def alert_reason(self) -> str:
        a = thresholds()["storage_alerts"]
        return (
            f"Alert levels (spec §8.2: L1 < {a['L1']} %, L2 < {a['L2']} %, L3 < {a['L3']} % FSC) need valid storage. "
            + self.storage_reason()
        )

    def unavailable_keys(self) -> dict[str, dict[str, str]]:
        storage, flows = self.storage_reason(), self.flow_reason()
        return {
            "timeseries": {
                "storage-trend": storage,
                "flows": flows,
                "cumulative-inflow": flows,
                "dam-storage": storage,
                "dam-flows": flows,
                "dam-monthly": storage,
                "rule-curve": "Operating rule curves are not in the database. " + storage,
                "dam-anomaly": storage,
                "low-storage-prob": "Storage probabilities need a forecast model and valid storage. " + storage,
            },
            "breakdown": {
                "by-wma": storage,
                "distribution": storage,
                "operational": self.alert_reason(),
                "wma-volume": storage,
                "by-province": storage,
                "risk-categories": storage,
            },
            "table": {
                "top10": "Dam capacities are not in the database (FSL is 1 200 m in every row). " + storage,
                "lowest": storage,
                "key-dams-forecast": "Storage forecasts need a forecast model and valid storage. " + storage,
                "risk-outlook": "Storage forecasts need a forecast model and valid storage. " + storage,
            },
            "map": {
                "storage": f"{NO_POINTS}. {storage}",
                "anomaly": f"{NO_POINTS}. {storage}",
                "inflow": f"{NO_POINTS}. {flows}",
                "status": f"{NO_POINTS}. {self.alert_reason()}",
                "forecast-12": f"{NO_POINTS}. Storage forecasts need a forecast model and valid storage.",
            },
        }

    # ---------------------------------------------------------------- contract endpoints

    async def summary(self) -> dict[str, Any]:
        storage, flows = self.storage_reason(), self.flow_reason()
        evap = self.reason("res_evaporation_mm_day")
        kpis = [
            self.kpi_unavailable("storage", "storagePct", "Total Storage", "% FSC", storage),
            self.kpi_unavailable("volume", "storageVolume", "Current Volume", "Mm³", storage),
            self.kpi_unavailable("inflow", "inflow", "Total Inflow", "m³/s", flows),
            self.kpi_unavailable("outflow", "outflow", "Total Outflow", "m³/s", flows),
            self.kpi_unavailable("evaporation", "evaporationLoss", "Evaporation Loss", "Mm³", evap),
            self.kpi_unavailable("below", "damsBelow", "Dams Below 25%", "dams", storage),
            self.kpi_unavailable("above", "damsAbove", "Dams Above 90%", "dams", storage),
            self.kpi_unavailable("alerts", "damAlerts", "Operational Alerts", "dams", self.alert_reason()),
        ]
        return self.kpi_set(kpis)

    async def timeseries(self, metric: str) -> dict[str, Any]:
        raise self.no_key("timeseries", metric, [], self.unavailable_keys()["timeseries"])

    async def forecast(self, metric: str) -> dict[str, Any]:
        if metric in ("storage", "flows"):
            raise self.forecast_none(metric)
        raise self.no_key("forecast", metric, [], {})

    async def breakdown(self, metric: str) -> dict[str, Any]:
        raise self.no_key("breakdown", metric, [], self.unavailable_keys()["breakdown"])

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        raise self.no_key("table", metric, [], self.unavailable_keys()["table"])

    async def map(self, layer: str) -> dict[str, Any]:
        """The contract's point layers are unavailable; the two live 8_1 columns are served as district layers
        (additions, CONTRACT_CHANGES §14.3)."""
        if layer == "catchment-rainfall":
            return await self.district_map(
                layer,
                "Catchment rainfall at the reservoirs (month total)",
                RAIN,
                "rainfallMonthly",
                nd=0,
                how="sum",
                unit="mm",
            )
        if layer == "soil-moisture":
            return await self.district_map(
                layer, "Catchment soil moisture at the reservoirs (month mean)", SM, "soilMoisture", nd=3
            )
        raise self.no_key("map", layer, ["catchment-rainfall", "soil-moisture"], self.unavailable_keys()["map"])

    # ---------------------------------------------------------------- entities: the district reservoirs of 8_1

    async def reservoirs(self) -> dict[str, dict[str, str]]:
        """MDB code → {name, river} from 8_1 (one combined reservoir per district; "Siyanda", the old name of
        Z F Mgcawu, is not a district alias and is skipped — DECISIONS D3)."""
        ds = self.registry.datasets[TABLE]
        loc = ident(ds.location.column)

        async def load() -> dict[str, dict[str, str]]:
            # one probe per district spelling on the (district_name, date) index: a few ms, where a scan of the
            # 520 000-row table took 5 s cold
            rows = await self.ctx.state.db.fetch(
                f"SELECT x.n, x.dam, x.river FROM unnest($1::text[]) AS w(name) CROSS JOIN LATERAL ("
                f"SELECT {loc} AS n, {ident('dam_name')} AS dam, {ident('river')} AS river FROM {ds.fq} "
                f"WHERE {loc} = w.name AND {ds.rows_sql()} ORDER BY {ident(str(ds.time.column))} DESC LIMIT 1) x",
                districts.db_names(),
            )
            out: dict[str, dict[str, str]] = {}
            for r in rows:
                d = districts.resolve(r["n"])
                if d is not None:
                    out[d.code] = {"name": str(r["dam"] or ""), "river": str(r["river"] or "")}
            return out

        res: dict[str, dict[str, str]] = await self.ctx.state.cache.get_or_set(
            "dams:reservoirs", self.ctx.state.settings.cache_ttl_meta_s, load
        )
        return res

    async def entities(self) -> dict[str, Any]:
        res = await self.reservoirs()
        sel = self.ctx.filters.scope
        items = [
            c.Entity(
                id=d.code,
                name=res[d.code]["name"] or d.official_name,
                type="dam",
                province=d.province,
                municipality=d.code,
                sub=f"{d.official_name} · {res[d.code]['river']}",
            )
            for d in sorted(districts.districts(), key=lambda d: res.get(d.code, {}).get("name") or d.official_name)
            if d.code in res and (sel.level == "national" or d.code in sel.district_codes)
        ]
        return c.EntityList(type="dam", total=len(items), items=items, source=[TABLE]).dump()

    async def entity(self, code: str) -> dict[str, Any]:
        d = self.district(code)
        res = (await self.reservoirs()).get(d.code)
        if res is None:
            raise NotFound(f"No reservoir for district {d.code!r} in {TABLE}")
        storage = self.storage_reason()
        rain = await self.field("Catchment rainfall, month total", RAIN, d.code, nd=0, how="sum")
        sm = await self.field("Catchment soil moisture, month mean", SM, d.code, nd=3)
        fields = [
            c.EntityField(label="District", value=d.official_name),
            c.EntityField(label="Province", value=districts.PROVINCES[d.province]),
            c.EntityField(label="River", value=res["river"]),
            c.EntityField(label="Storage level", value=None, unit="% FSC", scale="storage"),
            c.EntityField(label="Storage volume", value=None, unit="Mm³"),
            c.EntityField(label="Alert level (spec §8.2)", value=None),
            rain,
            sm,
        ]
        for f, why in (
            ("Storage level", storage),
            ("Storage volume", storage),
            ("Alert level (spec §8.2)", self.alert_reason()),
        ):
            self.ctx.explain(f"fields.{f}", why)
        dates = [f.as_of for f in (rain, sm) if f.as_of]
        body = c.EntityDetail(
            id=d.code,
            name=res["name"] or d.official_name,
            type="dam",
            province=d.province,
            municipality=d.code,
            sub=f"{d.official_name} · {districts.PROVINCES[d.province]} · {res['river']}",
            title=res["name"] or d.official_name,
            fields=fields,
            image="dam",
            as_of=max(dates) if dates else None,
            source=[TABLE],
        ).dump()
        return body


def alert_for(pct_fsc: float | None) -> str | None:
    """Spec §8.2 alert level of a valid % FSC (L1 < 50 %, L2 < 30 %, L3 < 15 %), "None" above L1; None without data."""
    if pct_fsc is None or pd.isna(pct_fsc):
        return None
    return storage_alert_level(float(pct_fsc))


async def prewarm(data: Any) -> None:
    for var in (RAIN, SM):  # entity cards: monthly totals / means of the two served columns
        await data.daily_monthly_stats(var, ())
