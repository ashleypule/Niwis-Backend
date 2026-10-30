"""Alerts & Early Warnings (contract `alerts`, spec §2.2; `GET /api/v1/alerts`, `/alerts/summary`, `/alerts/map`;
DECISIONS D44).

No alert records are stored, so alerts are **derived on request** from the latest complete period of the live data
and the thresholds in `app/core/thresholds.json`: a district raises an alert when it crosses a threshold. Every alert
is `open` (nothing is acknowledged or resolved in a database) and dated by the period it was computed for.

| Module | Rule (per district, latest complete month) | Severity |
| --- | --- | --- |
| drought | SPI-6 drought class D4 / D3 | critical |
| drought | D2 | high |
| drought | D1 | moderate |
| vegetation | VCI below 20 % (Very Poor) / below 40 % (Poor: stress) | high / moderate |
| water-quality | CCME WQI below 25 (Very Poor) / below 50 (Poor) | high / moderate |
| groundwater-reserve | exploitation index above 1.0 (over-exploited) / 0.7–1.0 (Very High) | high / moderate |
| green-drop | effluent compliance score below 30 % (Critical) / below 50 % (Poor) | high / moderate |
| blue-drop | SANS 241 compliance score below 50 % (Critical) / below 70 % (Poor) | high / moderate |
| services | non-revenue water above 40 % / above the 20 % target (latest complete year, 18.2) | high / moderate |
| rwqo | RWQO compliance below 30 % / 50 % (latest complete month) | high / moderate |
| iwrm | system water losses above 40 % / 20 % (latest year, 16.1) | high / moderate |
| tariffs | year-on-year municipal block tariff increase above 30 % / 15 % (19.2) | high / moderate |
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core import districts
from app.core.errors import BadRequest
from app.core.provenance import iso
from app.core.thresholds import drought_class, drought_label, exploitation_class, wqi_class
from app.modules.blue_drop.service import BlueDropService
from app.modules.common import Ctx, unknown
from app.modules.drought.service import DroughtService
from app.modules.green_drop.service import GreenDropService
from app.modules.groundwater_reserve.service import GroundwaterReserveService
from app.modules.vegetation.service import VegetationService
from app.modules.water_quality.service import WaterQualityService
from app.schemas import contract as c

MODULE = "alerts"
SEVERITIES = ("critical", "high", "moderate", "low", "info")
RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEVERITY_COLORS = {"critical": "#730000", "high": "#E60000", "moderate": "#FFAA00", "low": "#FCD37F", "info": "#9E9E9E"}
NOTE = (
    "Derived on request from the latest complete period of the live data and the thresholds in thresholds.json; "
    "no alert register is stored, so every alert is open and nothing is acknowledged or resolved (DECISIONS D44)."
)


class AlertsService:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    # ---------------------------------------------------------------- rules

    async def compute(self) -> list[dict[str, Any]]:
        """Every alert of every district (cached until the newest data date changes); scope filters apply later."""

        async def load() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for rule in (
                self.drought,
                self.vegetation,
                self.water_quality,
                self.groundwater,
                self.green_drop,
                self.blue_drop,
                self.services_nrw,
                self.rwqo_compliance,
                self.iwrm_losses,
                self.tariff_increase,
            ):
                try:
                    out += await rule()
                except Exception:  # one module's data problem never hides the other modules' alerts
                    import logging

                    logging.getLogger("niwis.alerts").exception("alert rule failed", extra={"rule": rule.__name__})
            return out

        res: list[dict[str, Any]] = await self.ctx.state.cache.get_or_set(
            f"alerts:{self.ctx.newest_db}", self.ctx.state.settings.cache_ttl_summary_s, load
        )
        return res

    def alert(
        self, module: str, code: str, when: pd.Timestamp, severity: str, title: str, message: str, rule: str
    ) -> dict[str, Any] | None:
        d = districts.by_code(str(code))
        if d is None:
            return None
        return {
            "id": f"{module}:{rule}:{d.code}:{when:%Y-%m}",
            "date": iso(when.date()) or "",
            "module": module,
            "severity": severity,
            "title": title,
            "message": message,
            "location": d.official_name,
            "province": d.province,
            "entityId": d.code,
            "status": "open",
        }

    async def drought(self) -> list[dict[str, Any]]:
        svc = DroughtService(self.ctx)
        m = await svc.latest("spi_6")
        out = []
        for code, v in (await svc.at("spi_6", m)).dropna().items():
            cls = drought_class(float(v))
            if cls in ("D3", "D4"):
                sev = "critical"
            elif cls == "D2":
                sev = "high"
            elif cls == "D1":
                sev = "moderate"
            else:
                continue
            a = self.alert(
                "drought",
                str(code),
                pd.Timestamp(m),
                sev,
                drought_label(cls),
                f"SPI-6 {float(v):.2f} in {m:%b %Y} ({drought_label(cls)}, spec §3.4).",
                "spi6",
            )
            if a:
                out.append(a)
        return out

    async def vegetation(self) -> list[dict[str, Any]]:
        svc = VegetationService(self.ctx)
        m = await svc.last("vci")
        out = []
        for code, v in (await svc.at("vci", m)).dropna().items():
            if float(v) < 20:
                sev, label = "high", "Vegetation condition Very Poor"
            elif float(v) < 40:
                sev, label = "moderate", "Vegetation stress"
            else:
                continue
            a = self.alert(
                "vegetation",
                str(code),
                pd.Timestamp(m),
                sev,
                label,
                f"VCI {float(v):.0f} % in {m:%b %Y} (below 40 % = drought stress, Kogan 1995).",
                "vci",
            )
            if a:
                out.append(a)
        return out

    async def water_quality(self) -> list[dict[str, Any]]:
        wqi, m = await WaterQualityService(self.ctx).district_wqi()
        out = []
        for code, v in wqi.dropna().items():
            cls = wqi_class(float(v))
            if cls == "Very Poor":
                sev = "high"
            elif cls == "Poor":
                sev = "moderate"
            else:
                continue
            a = self.alert(
                "water-quality",
                str(code),
                pd.Timestamp(m),
                sev,
                f"Water quality {cls}",
                f"CCME WQI {float(v):.0f} over the 12 months to {m:%b %Y} ({cls}).",
                "wqi",
            )
            if a:
                out.append(a)
        return out

    async def groundwater(self) -> list[dict[str, Any]]:
        svc = GroundwaterReserveService(self.ctx)
        yr = await svc.year()
        out = []
        for code, v in (await svc.ei_by_district(yr)).dropna().items():
            cls = exploitation_class(float(v) / 100)
            if cls == "Over-exploited":
                sev = "high"
            elif cls == "Very High":
                sev = "moderate"
            else:
                continue
            a = self.alert(
                "groundwater-reserve",
                str(code),
                yr,
                sev,
                f"Aquifer exploitation {cls}",
                f"Exploitation index {float(v) / 100:.2f} in {yr:%Y} (abstraction / recharge, spec §11.3).",
                "ei",
            )
            if a:
                out.append(a)
        return out

    async def compliance(
        self, svc: Any, module: str, title: str, poor: float, critical: float, what: str
    ) -> list[dict[str, Any]]:
        last = await svc.last()
        out = []
        for code, v in (await svc.district_scores(last)).dropna().items():
            if float(v) < critical:
                sev = "high"
            elif float(v) < poor:
                sev = "moderate"
            else:
                continue
            a = self.alert(
                module,
                str(code),
                last,
                sev,
                title,
                f"{what} {float(v):.0f} % in {last:%b %Y} ({svc.band(float(v))}).",
                "score",
            )
            if a:
                out.append(a)
        return out

    async def green_drop(self) -> list[dict[str, Any]]:
        return await self.compliance(
            GreenDropService(self.ctx), "green-drop", "Effluent non-compliance", 50, 30, "Effluent compliance score"
        )

    async def blue_drop(self) -> list[dict[str, Any]]:
        return await self.compliance(
            BlueDropService(self.ctx), "blue-drop", "Drinking-water non-compliance", 70, 50, "SANS 241 compliance score"
        )

    async def threshold_rule(
        self,
        module: str,
        per: pd.Series,
        when: pd.Timestamp,
        *,
        high: float,
        moderate: float,
        title: str,
        what: str,
        rule: str,
        unit: str = "%",
        above: bool = True,
        fmt: str = "%Y",
    ) -> list[dict[str, Any]]:
        """Alerts for every district whose value is beyond `moderate` (moderate) or `high` (high); `above` False
        inverts the comparison (a value too low)."""
        out = []
        for code, v in per.dropna().items():
            x = float(v)
            if (x > high) if above else (x < high):
                sev = "high"
            elif (x > moderate) if above else (x < moderate):
                sev = "moderate"
            else:
                continue
            a = self.alert(module, str(code), when, sev, title, f"{what} {x:.1f} {unit} in {when:{fmt}}.", rule)
            if a:
                out.append(a)
        return out

    async def services_nrw(self) -> list[dict[str, Any]]:
        from app.modules.services.service import INPUT_L, NRW_L, NRW_TARGET_PCT, ServicesService

        svc = ServicesService(self.ctx)
        yr = await svc.ops_year()
        nrw, inp = await svc.district_values(NRW_L, yr), await svc.district_values(INPUT_L, yr)
        both = nrw.index.intersection(inp.index)
        pct = pd.Series(
            {
                k: float(nrw[k]) / float(inp[k]) * 100
                for k in both
                if pd.notna(nrw[k]) and pd.notna(inp[k]) and float(inp[k]) > 0
            },
            dtype=float,
        )
        return await self.threshold_rule(
            "services",
            pct,
            yr,
            high=2 * NRW_TARGET_PCT,
            moderate=NRW_TARGET_PCT,
            title="Non-revenue water above target",
            what="Non-revenue water",
            rule="nrw",
        )

    async def rwqo_compliance(self) -> list[dict[str, Any]]:
        from app.modules.rwqo.service import RwqoService

        svc = RwqoService(self.ctx)
        last = await svc.last()
        return await self.threshold_rule(
            "rwqo",
            await svc.district_scores(last),
            last,
            high=30,
            moderate=50,
            title="Resource quality objectives not met",
            what="RWQO compliance",
            rule="compliance",
            above=False,
            fmt="%b %Y",
        )

    async def iwrm_losses(self) -> list[dict[str, Any]]:
        from app.modules.iwrm.service import LOSSES, IwrmService

        svc = IwrmService(self.ctx)
        yr = await svc.year()
        return await self.threshold_rule(
            "iwrm",
            await svc.district_values(LOSSES, yr),
            yr,
            high=40,
            moderate=20,
            title="High system water losses",
            what="System water losses",
            rule="losses",
        )

    async def tariff_increase(self) -> list[dict[str, Any]]:
        from app.modules.tariffs.service import BLOCK, TariffsService

        svc = TariffsService(self.ctx)
        yr = await svc.year()
        return await self.threshold_rule(
            "tariffs",
            await svc.increase(BLOCK, yr),
            yr,
            high=30,
            moderate=15,
            title="Steep municipal tariff increase",
            what="Year-on-year municipal block tariff increase",
            rule="increase",
        )

    # ---------------------------------------------------------------- views

    def in_scope(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sel = self.ctx.filters.scope
        if sel.level == "national":
            return items
        codes = set(sel.district_codes)
        return [a for a in items if a["entityId"] in codes]

    async def list(self, module: str | None, severity: str | None, province: str | None) -> dict[str, Any]:
        if severity and severity not in SEVERITIES:
            raise BadRequest(
                f"Unknown severity {severity!r}", issues=[{"param": "severity", "allowed": list(SEVERITIES)}]
            )
        if province and province.upper() not in districts.PROVINCES:
            raise BadRequest(
                f"Unknown province {province!r}", issues=[{"param": "province", "allowed": sorted(districts.PROVINCES)}]
            )
        items = self.in_scope(await self.compute())
        if module:
            items = [a for a in items if a["module"] == module]
        if severity:
            items = [a for a in items if a["severity"] == severity]
        if province:
            items = [a for a in items if a["province"] == province.upper()]
        items.sort(key=lambda a: (RANK[a["severity"]], a["module"], a["location"]))
        return c.AlertList(total=len(items), unread=len(items), items=[c.Alert(**a) for a in items]).dump()

    async def summary(self) -> dict[str, Any]:
        items = self.in_scope(await self.compute())

        def by(sev: str) -> float:
            return float(sum(1 for a in items if a["severity"] == sev))

        when = max((a["date"] for a in items), default=iso(self.ctx.newest_db) or "")
        kpis = [
            c.Kpi(
                id="active",
                metric="damAlerts",
                label="Active Alerts",
                value=float(len(items)),
                unit="alerts",
                decimals=0,
                sublabel="districts beyond a threshold in the latest complete period",
                as_of=when,
            ),
            c.Kpi(
                id="critical",
                metric="damAlerts",
                label="Critical",
                value=by("critical"),
                unit="alerts",
                decimals=0,
                as_of=when,
            ),
            c.Kpi(id="high", metric="damAlerts", label="High", value=by("high"), unit="alerts", decimals=0, as_of=when),
            c.Kpi(
                id="open",
                metric="damAlerts",
                label="Open",
                value=float(len(items)),
                unit="alerts",
                decimals=0,
                sublabel="every derived alert is open: no acknowledgements are stored",
                as_of=when,
            ),
            c.Kpi(
                id="modules",
                metric="recordsIngested",
                label="Modules Raising Alerts",
                value=float(len({a["module"] for a in items})),
                unit="modules",
                decimals=0,
                as_of=when,
            ),
            c.Kpi(
                id="provinces",
                metric="recordsIngested",
                label="Provinces Affected",
                value=float(len({a["province"] for a in items})),
                unit="provinces",
                decimals=0,
                as_of=when,
            ),
        ]
        kpis[0].sublabel = f"{kpis[0].sublabel}. {NOTE}"
        return c.KpiSet(module=MODULE, as_of=when, scope=self.ctx.scope, kpis=kpis, aggregation="district").dump()

    async def map(self, layer: str) -> dict[str, Any]:
        if layer != "locations":
            raise unknown("layer", layer, ["locations"])
        items = self.in_scope(await self.compute())
        worst: dict[str, str] = {}
        for a in items:
            code = a["entityId"]
            if code not in worst or RANK[a["severity"]] < RANK[worst[code]]:
                worst[code] = a["severity"]
        keep = set(self.ctx.filters.scope.district_codes)
        values: dict[str, Any] = {code: sev for code, sev in worst.items() if code in keep}
        if not values:
            self.ctx.explain("values", "No district of this scope is beyond a threshold")
        when = max((a["date"] for a in items), default=iso(self.ctx.newest_db) or "")
        return c.MapLayer(
            id=layer,
            title="Alert severity by district (worst alert)",
            kind="choropleth",
            level="municipality",
            scale="severity",
            unit="—",
            date=when,
            values=values,
            geometry="districts",
            as_of=when,
            aggregation="district",
        ).dump()
