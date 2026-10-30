"""Data Integration (`data-integration`) and Data Governance & QA (`governance`) pages, served from what the API
already knows about its database: the registered tables (rows, districts, first / last dates, completeness,
freshness), the registry roles (live, withheld, duplicate columns with their reasons) and the catalogue
(DECISIONS D45). The same facts for every scope: they describe the database, not an area.

Served from the same facts (DECISIONS D47): records per month by source group (rows dated in the month: the tables
carry no load timestamp), the districts each table covers (the coverage map), the providers named in the tables'
names (SANSA, SAWS; the other tables carry no source) and the retention policy of the specification (§23.3).

Not in the database, hence 501 with the reason: access requests and usage, standards-compliance scores,
data-provider locations.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

import pandas as pd

from app.core import districts
from app.core.provenance import iso
from app.core.stats import rnd
from app.modules.common import Ctx, unknown
from app.registry.catalogue import get_catalogue
from app.schemas import contract as c

log = logging.getLogger("niwis.meta_pages")
NO_PROVIDERS = "Data providers and feeds as organisations are not recorded in the database (tables carry no source)"
PROVIDERS = {  # organisations named in the registered tables' names (the only provider record the database holds)
    "sansa": ("SANSA", "South African National Space Agency", "Earth observation"),
    "saws": ("SAWS", "South African Weather Service", "Meteorology"),
}
UNNAMED = "Not named (the table carries no source)"
INGESTION_MONTHS = 24
SOURCE_GROUPS = [  # ingestion chart series
    ("sansa", "Earth observation (SANSA)"),
    ("saws", "Meteorology (SAWS)"),
    ("municipal", "Municipal tables (local-municipality rows)"),
    ("other", "Other tables (source not recorded)"),
]
RETENTION = [  # NIWIS specification §23.3: a policy, not observed from the database
    ("Real-time data", "15-minute to daily records", "2 years", "DWS cloud"),
    ("Historical archive", "Daily and monthly records", "Indefinitely", "National Data Archive"),
    ("Satellite imagery", "Earth-observation products", "30-year archive", "SANSA EO archive"),
    ("Model outputs", "Forecasts and derived indicators", "5 years", "NIWIS database"),
    ("Audit logs", "Access and change logs", "7 years", "NIWIS security system"),
]


def provider_of(table: str) -> tuple[str, str, str] | None:
    low = table.lower()
    return next((v for k, v in PROVIDERS.items() if k in low), None)


def source_group(ds: Any) -> str:
    """Key of a table's source group in the ingestion chart."""
    p = provider_of(ds.table)
    if p:
        return p[0].lower()
    return "municipal" if ds.sub_district else "other"


NO_USAGE = "Access requests and usage are not recorded (the API keeps no user log)"
MODULE_GROUPS = {  # db module → dashboard group of the inventory breakdown
    1: "Drought",
    2: "Climate",
    3: "Vegetation",
    4: "Rainfall",
    5: "Runoff",
    6: "Dams",
    7: "Surface water",
    8: "Groundwater",
    9: "Groundwater reserve",
    10: "Water quality",
    11: "Wastewater",
    12: "Drinking water",
    13: "RWQO",
    14: "IWRM",
    17: "Water quantity",
}


class MetaPageService:
    MODULE = ""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.state = ctx.state
        self.registry = ctx.state.registry

    # ---------------------------------------------------------------- facts

    async def facts(self) -> dict[str, Any]:
        src = await self.state.meta.sources()
        items = src["items"]
        newest = dt.date.fromisoformat(src["newestDataDate"]) if src["newestDataDate"] else None
        existing = [t for t in items if t["exists"]]
        latency = [
            (newest - dt.date.fromisoformat(t["coveredTo"])).days for t in existing if t.get("coveredTo") and newest
        ]
        completeness = [t["completenessPct"] for t in existing if t.get("completenessPct") is not None]
        cols = [(ds.table, n, col) for ds in self.registry.datasets.values() for n, col in ds.columns.items()]
        live = sum(1 for _, _, col in cols if col.role == "canonical")
        withheld = [(t, n, col) for t, n, col in cols if col.role == "withheld"]
        dups = sum(1 for _, _, col in cols if col.role == "duplicate")
        return {
            "newest": newest,
            "items": items,
            "existing": existing,
            "tables": len(items),
            "feeds": len(existing),
            "stale": sum(1 for t in existing if t.get("isStale")),
            "rows": sum(t.get("rows") or 0 for t in existing),
            "domains": len({t["dbModule"] for t in items}),
            "latency_days": sum(latency) / len(latency) if latency else None,
            "completeness": sum(completeness) / len(completeness) if completeness else None,
            "live_cols": live,
            "withheld": withheld,
            "duplicates": dups,
            "cols": len(cols),
            "districts_pct": (
                sum(
                    min(100.0, (t.get("districts") or 0) / 52 * 100)
                    for t in existing
                    if not self.registry.datasets[t["table"]].national
                )
                / max(1, sum(1 for t in existing if not self.registry.datasets[t["table"]].national))
            ),
        }

    def quality_dimensions(self, f: dict[str, Any]) -> list[tuple[str, str, float | None]]:
        valid = f["live_cols"] / f["cols"] * 100 if f["cols"] else None
        fresh = (1 - f["stale"] / f["feeds"]) * 100 if f["feeds"] else None
        return [
            ("completeness", "Completeness (rows present per district-period)", rnd(f["completeness"], 1)),
            ("validity", "Validity (columns served after the plausibility checks)", rnd(valid, 1)),
            ("timeliness", "Timeliness (tables not stale)", rnd(fresh, 1)),
            ("coverage", "Coverage (districts present per table)", rnd(f["districts_pct"], 1)),
        ]

    # ---------------------------------------------------------------- providers, ingestion, coverage (D47)

    def providers(self, f: dict[str, Any]) -> list[dict[str, Any]]:
        """One row per organisation named in the tables' names, and one for the tables that name none."""
        rows: dict[str, dict[str, Any]] = {}
        for t in f["items"]:
            p = provider_of(t["table"])
            key, name, kind = p if p else ("unnamed", UNNAMED, None)
            r = rows.setdefault(
                key,
                {
                    "id": key.lower(),
                    "provider": key if p else name,
                    "type": f"{kind} ({name})" if p else None,
                    "feeds": 0.0,
                    "online": 0.0,
                    "protocol": None,
                    "format": "PostgreSQL table",
                    "cadences": set(),
                },
            )
            r["feeds"] += 1
            r["online"] += 1 if t["exists"] and not t.get("isStale") else 0
            r["cadences"].add(t["resolution"])
        out = []
        for r in sorted(rows.values(), key=lambda x: (x["id"] == "unnamed", x["id"])):
            cadences = r.pop("cadences")
            r["cadence"] = ", ".join(x for x in ("daily", "monthly", "annual") if x in cadences)
            out.append(r)
        return out

    async def providers_table(self, f: dict[str, Any], title: str) -> dict[str, Any]:
        rows = self.providers(f)
        self.ctx.explain("rows.protocol", "Transfer protocols are not recorded (the tables are read in place)")
        if all(r["type"] is None for r in rows):
            self.ctx.explain("rows.type", NO_PROVIDERS)
        return c.TableSet(
            id="providers",
            title=title,
            columns=[
                c.Column(key="provider", label="Provider", type="text"),
                c.Column(key="type", label="Type", type="text"),
                c.Column(key="feeds", label="Tables", type="number", decimals=0, align="right"),
                c.Column(key="online", label="Online (fresh)", type="number", decimals=0, align="right"),
                c.Column(key="protocol", label="Protocol", type="text"),
                c.Column(key="format", label="Format", type="text"),
                c.Column(key="cadence", label="Cadence", type="text"),
            ],
            rows=rows,
            row_key="id",
            total=len(rows),
            as_of=iso(f["newest"]),
        ).dump()

    def providers_kpi(self, f: dict[str, Any], when: dt.date | None) -> c.Kpi:
        named = [r for r in self.providers(f) if r["id"] != "unnamed"]
        unnamed = f["tables"] - int(sum(r["feeds"] for r in named))
        return self.kpi(
            "providers",
            "recordsIngested",
            "Data Providers",
            float(len(named)),
            "providers",
            when,
            sublabel=f"organisations named in the tables' names ({', '.join(r['provider'] for r in named)}); "
            f"{unnamed} tables carry no source",
        )

    async def monthly_rows(self, f: dict[str, Any]) -> dict[str, dict[str, int]]:
        """Rows per month (dated by their own period) for every reachable table over the last INGESTION_MONTHS
        months, cached like the table statistics."""
        newest = f["newest"]
        if newest is None:
            return {}
        start = (pd.Timestamp(newest) - pd.DateOffset(months=INGESTION_MONTHS - 1)).replace(day=1).date()

        async def load() -> dict[str, dict[str, int]]:
            sem = asyncio.Semaphore(2)

            async def one(table: str) -> tuple[str, dict[str, int]]:
                ds = self.registry.datasets[table]
                d = ds.date_sql()
                sql = (
                    f"SELECT date_trunc('month', {d})::date AS m, count(*) AS n FROM {ds.fq} "
                    f"WHERE {ds.rows_sql()} AND {d} >= $1 GROUP BY 1"
                )
                async with sem:
                    try:
                        rows = await self.state.db.fetch_slow(sql, start)
                    except Exception:
                        return table, {}
                return table, {r["m"].isoformat(): int(r["n"]) for r in rows}

            pairs = await asyncio.gather(*(one(t["table"]) for t in f["existing"]))
            return dict(pairs)

        res: dict[str, dict[str, int]] = await self.state.cache.get_or_set(
            f"meta:monthly_rows:{newest}", self.state.settings.cache_ttl_meta_s, load
        )
        return res

    async def ingestion(self, f: dict[str, Any]) -> dict[str, Any]:
        per = await self.monthly_rows(f)
        newest = f["newest"]
        months = (
            [
                (pd.Timestamp(newest).replace(day=1) - pd.DateOffset(months=k)).date().isoformat()
                for k in range(INGESTION_MONTHS - 1, -1, -1)
            ]
            if newest
            else []
        )
        totals: dict[str, dict[str, int]] = {k: dict.fromkeys(months, 0) for k, _ in SOURCE_GROUPS}
        for table, counts in per.items():
            key = source_group(self.registry.datasets[table])
            for m, n in counts.items():
                if m in totals[key]:
                    totals[key][m] += n
        if not any(sum(v.values()) for v in totals.values()):
            self.ctx.explain("values", "No rows dated in the last two years")
        return c.SeriesSet(
            id="ingestion",
            title=f"Records per month by source group (rows dated in the month, last {INGESTION_MONTHS} months)",
            x_type="time",
            axes=[c.Axis(unit="rows", label="Records")],
            series=[
                c.Series(
                    key=key,
                    name=name,
                    kind="bar",
                    stack="sources",
                    data=[[m, float(totals[key][m])] for m in months],
                )
                for key, name in SOURCE_GROUPS
            ],
            as_of=iso(newest),
            note="Rows counted by the period they are dated for (annual tables fall in January): the database keeps "
            "no load log, so this is the data's own cadence, not when it arrived.",
        ).dump()

    async def coverage_map(self, f: dict[str, Any]) -> dict[str, Any]:
        """Per district: % of the reachable district-keyed tables holding at least one row for it."""
        newest = f["newest"]

        async def load() -> dict[str, float]:
            tables = [
                self.registry.datasets[t["table"]]
                for t in f["existing"]
                if not self.registry.datasets[t["table"]].national
            ]
            hits: dict[str, int] = dict.fromkeys((d.code for d in districts.districts()), 0)
            for ds in tables:
                try:
                    found = await self.state.data.spellings(ds)
                except Exception:
                    log.warning("coverage map: district discovery failed", extra={"table": ds.table})
                    continue
                for code in found:
                    if code in hits:
                        hits[code] += 1
            return {code: n / len(tables) * 100 for code, n in hits.items()} if tables else {}

        pct: dict[str, float] = await self.state.cache.get_or_set(
            f"meta:coverage_map:{newest}", self.state.settings.cache_ttl_meta_s, load
        )
        sel = self.ctx.filters.scope
        keep = set(sel.district_codes) if sel.level != "national" else set(pct)
        values: dict[str, c.Cell] = {code: rnd(v, 0) for code, v in pct.items() if code in keep}
        if not values:
            self.ctx.explain("values", "No reachable district-keyed tables")
        return c.MapLayer(
            id="coverage",
            title="Tables with data for the district (% of the district-keyed tables)",
            kind="choropleth",
            level="municipality",
            scale="coverage",
            unit="%",
            date=iso(newest) or "",
            values=values,
            as_of=iso(newest),
            geometry="districts",
        ).dump()

    def score(self, f: dict[str, Any]) -> float | None:
        vals = [v for _, _, v in self.quality_dimensions(f) if v is not None]
        return sum(vals) / len(vals) if vals else None

    def kpi(
        self,
        kid: str,
        metric: str,
        label: str,
        value: float | None,
        unit: str | None,
        when: dt.date | None,
        *,
        decimals: int = 0,
        sublabel: str | None = None,
        reason: str | None = None,
    ) -> c.Kpi:
        if value is None:
            self.ctx.explain(f"kpis.{kid}", reason or "Not recorded in the database")
        return c.Kpi(
            id=kid,
            metric=metric,
            label=label,
            value=rnd(value, decimals),
            **({"sublabel": sublabel} if sublabel else {}),
            **({"unit": unit} if unit else {}),
            decimals=decimals,
            as_of=iso(when),
            **({"unavailable_reason": reason} if value is None and reason else {}),
        )

    def kpi_set(self, kpis: list[c.Kpi], when: dt.date | None) -> dict[str, Any]:
        return c.KpiSet(module=self.MODULE, as_of=iso(when) or "", scope=self.ctx.scope, kpis=kpis).dump()

    def category_set(
        self, cid: str, title: str, unit: str, cats: list[str], series: list[c.CategorySeries], when: dt.date | None
    ) -> dict[str, Any]:
        return c.CategorySet(id=cid, title=title, unit=unit, categories=cats, series=series, as_of=iso(when)).dump()

    async def quality_table(self, f: dict[str, Any]) -> dict[str, Any]:
        rows = [{"id": k, "dimension": label, "score": v} for k, label, v in self.quality_dimensions(f)]
        return c.TableSet(
            id="quality",
            title="Data quality dimensions (computed from the table statistics and the registry)",
            columns=[
                c.Column(key="dimension", label="Dimension", type="text"),
                c.Column(key="score", label="Score (%)", type="number", unit="%", decimals=1),
            ],
            rows=rows,
            row_key="id",
            as_of=iso(f["newest"]),
        ).dump()

    async def forecast(self, metric: str) -> dict[str, Any]:
        raise unknown("metric", metric, [])


class DataIntegrationService(MetaPageService):
    MODULE = "data-integration"

    async def summary(self) -> dict[str, Any]:
        f = await self.facts()
        when = f["newest"]
        kpis = [
            self.providers_kpi(f, when),
            self.kpi(
                "feeds",
                "recordsIngested",
                "Active Data Feeds",
                float(f["feeds"]),
                "tables",
                when,
                sublabel=f"registered tables reachable in the database ({f['stale']} stale)",
            ),
            self.kpi(
                "records",
                "recordsIngested",
                "Records (all tables)",
                float(f["rows"]),
                "rows",
                when,
                sublabel="row counts of the registered tables (no ingestion log: the 12-month count is not recorded)",
            ),
            self.kpi(
                "domains",
                "recordsIngested",
                "Data Domains",
                float(f["domains"]),
                "modules",
                when,
                sublabel="database modules (spec sections) with registered tables",
            ),
            self.kpi(
                "latency",
                "feedLatency",
                "Average Data Latency",
                f["latency_days"],
                "days",
                when,
                decimals=0,
                sublabel="mean days between a table's newest covered day and the newest data date",
            ),
            self.kpi(
                "quality",
                "dataQuality",
                "Data Quality Score",
                self.score(f),
                "%",
                when,
                decimals=1,
                sublabel="mean of completeness, validity, timeliness and coverage",
            ),
        ]
        return self.kpi_set(kpis, when)

    async def timeseries(self, metric: str) -> dict[str, Any]:
        if metric == "ingestion":
            return await self.ingestion(await self.facts())
        raise unknown("metric", metric, ["ingestion"])

    async def breakdown(self, metric: str) -> dict[str, Any]:
        f = await self.facts()
        when = f["newest"]
        if metric == "source-status":
            missing = f["tables"] - f["feeds"]
            withheld_only = sum(1 for t in f["existing"] if not t["variables"]["canonical"])
            live = f["feeds"] - f["stale"] - withheld_only
            return self.category_set(
                "source-status",
                "Registered tables by status",
                "feeds",
                ["Live", "Stale", "Withheld only", "Missing"],
                [
                    c.CategorySeries(
                        key="feeds",
                        name="Feeds",
                        values=[float(live), float(f["stale"]), float(withheld_only), float(missing)],
                    )
                ],
                when,
            )
        if metric == "by-type":
            res = ["daily", "monthly", "annual"]
            vals = [float(sum(t.get("rows") or 0 for t in f["existing"] if t["resolution"] == r)) for r in res]
            return self.category_set(
                "by-type",
                "Rows by table resolution",
                "records",
                ["Daily tables", "Monthly tables", "Annual tables"],
                [c.CategorySeries(key="rows", name="Records", values=vals)],
                when,
            )
        raise unknown("metric", metric, ["source-status", "by-type"])

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        f = await self.facts()
        if metric == "pipelines":
            rows = []
            for t in sorted(f["items"], key=lambda x: x["table"]):
                status = (
                    "missing"
                    if not t["exists"]
                    else "stale"
                    if t.get("isStale")
                    else "withheld"
                    if not t["variables"]["canonical"]
                    else "live"
                )
                rows.append(
                    {
                        "id": t["table"],
                        "pipeline": t["table"],
                        "source": t["module"],
                        "records": float(t.get("rows") or 0),
                        "lastUpdated": t.get("asOf"),
                        "status": status,
                        "withheld": float(len(t["variables"]["withheld"])),
                    }
                )
            return c.TableSet(
                id="pipelines",
                title="Registered tables: rows, newest record and status",
                columns=[
                    c.Column(key="pipeline", label="Pipeline (table)", type="text"),
                    c.Column(key="source", label="Source (module)", type="text"),
                    c.Column(key="records", label="Records", type="number", decimals=0, align="right"),
                    c.Column(key="lastUpdated", label="Last updated", type="date"),
                    c.Column(key="status", label="Status", type="text"),
                    c.Column(key="withheld", label="Withheld columns", type="number", decimals=0, align="right"),
                ],
                rows=rows[: limit or len(rows)],
                row_key="id",
                total=len(rows),
                as_of=iso(f["newest"]),
            ).dump()
        if metric == "quality":
            return await self.quality_table(f)
        if metric == "providers":
            return await self.providers_table(
                f, "Data providers named in the registered tables (the other tables carry no source)"
            )
        raise unknown("metric", metric, ["pipelines", "quality", "providers"])

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "coverage":
            return await self.coverage_map(await self.facts())
        raise unknown("layer", layer, ["coverage"])


class GovernanceService(MetaPageService):
    MODULE = "governance"

    async def summary(self) -> dict[str, Any]:
        f = await self.facts()
        when = f["newest"]
        kpis = [
            self.kpi(
                "datasets",
                "recordsIngested",
                "Total Datasets",
                float(f["tables"]),
                "tables",
                when,
                sublabel="registered tables",
            ),
            self.providers_kpi(f, when),
            self.kpi("feeds", "recordsIngested", "Active Data Feeds", float(f["feeds"]), "tables", when),
            self.kpi(
                "metadata",
                "dataQuality",
                "Datasets with Metadata",
                float(len(get_catalogue().categories)),
                "tables",
                when,
                sublabel="every registered table is catalogued with labels, units and its spec section",
            ),
            self.kpi(
                "quality",
                "dataQuality",
                "Data Quality Score (Overall)",
                self.score(f),
                "%",
                when,
                decimals=1,
                sublabel="mean of completeness, validity, timeliness and coverage",
            ),
            self.kpi(
                "standards",
                "dataQuality",
                "Standards Compliance",
                None,
                "%",
                when,
                reason="Standards-compliance assessments are not recorded in the database",
            ),
            self.kpi(
                "access", "recordsIngested", "Access Requests (This Year)", None, "requests", when, reason=NO_USAGE
            ),
            self.kpi(
                "incidents",
                "damAlerts",
                "Data Incidents",
                float(len(f["withheld"])),
                "columns",
                when,
                sublabel="columns withheld after the plausibility checks (DECISIONS D1): see the issues table",
            ),
        ]
        return self.kpi_set(kpis, when)

    async def timeseries(self, metric: str) -> dict[str, Any]:
        raise unknown("metric", metric, [])

    async def breakdown(self, metric: str) -> dict[str, Any]:
        f = await self.facts()
        when = f["newest"]
        if metric == "inventory":
            groups: dict[str, int] = {}
            for ds in self.registry.datasets.values():
                groups[MODULE_GROUPS.get(ds.db_module, f"Module {ds.db_module}")] = (
                    groups.get(MODULE_GROUPS.get(ds.db_module, f"Module {ds.db_module}"), 0) + 1
                )
            return self.category_set(
                "inventory",
                "Registered tables by dashboard group",
                "datasets",
                list(groups),
                [c.CategorySeries(key="datasets", name="Datasets", values=[float(v) for v in groups.values()])],
                when,
            )
        if metric == "frequency":
            res = {"daily": "Daily", "monthly": "Monthly", "annual": "Annual"}
            counts = {
                label: float(sum(1 for ds in self.registry.datasets.values() if ds.time.resolution == r))
                for r, label in res.items()
            }
            return self.category_set(
                "frequency",
                "Registered tables by update frequency",
                "datasets",
                list(counts),
                [c.CategorySeries(key="datasets", name="Datasets", values=list(counts.values()))],
                when,
            )
        if metric == "metadata":
            cat = get_catalogue()
            n = len(cat.categories)
            with_units = sum(1 for x in cat.categories if any(col.unit for col in x.columns)) / n * 100 if n else None
            with_spec = sum(1 for x in cat.categories if x.spec) / n * 100 if n else None
            with_issues = sum(1 for ds in self.registry.datasets.values() if ds.known_issues) / n * 100 if n else None
            return self.category_set(
                "metadata",
                "Metadata held per registered table",
                "%",
                ["Units and labels", "Spec section", "Known issues recorded"],
                [
                    c.CategorySeries(
                        key="datasets",
                        name="Datasets",
                        values=[rnd(with_units, 0), rnd(with_spec, 0), rnd(with_issues, 0)],
                    )
                ],
                when,
            )
        if metric in ("standards", "access"):
            raise unknown_501(
                NO_USAGE if metric == "access" else "Standards-compliance assessments are not recorded in the database",
                f"breakdown:governance:{metric}",
            )
        raise unknown("metric", metric, ["inventory", "frequency", "metadata", "standards", "access"])

    async def table(self, metric: str, limit: int | None) -> dict[str, Any]:
        f = await self.facts()
        if metric == "quality":
            return await self.quality_table(f)
        if metric == "issues":
            rows = [
                {
                    "id": f"{t}.{n}",
                    "date": iso(f["newest"]),
                    "dataset": t,
                    "provider": None,
                    "issueType": "withheld column",
                    "severity": "high",
                    "status": "withheld",
                    "reason": col.reason,
                }
                for t, n, col in f["withheld"]
            ]
            self.ctx.explain("rows.provider", NO_PROVIDERS)
            return c.TableSet(
                id="issues",
                title="Data issues: columns withheld after the plausibility checks",
                columns=[
                    c.Column(key="date", label="Date", type="date"),
                    c.Column(key="dataset", label="Dataset", type="text"),
                    c.Column(key="provider", label="Provider", type="text"),
                    c.Column(key="issueType", label="Issue type", type="text"),
                    c.Column(key="severity", label="Severity", type="text"),
                    c.Column(key="status", label="Status", type="text"),
                    c.Column(key="reason", label="Reason", type="text"),
                ],
                rows=rows[: limit or len(rows)],
                row_key="id",
                total=len(rows),
                as_of=iso(f["newest"]),
            ).dump()
        if metric == "providers":
            return await self.providers_table(
                f, "Data providers named in the registered tables (the other tables carry no source)"
            )
        if metric == "retention":
            rows = [
                {"id": f"r{i}", "dataClass": cls, "covers": what, "retention": keep, "location": where}
                for i, (cls, what, keep, where) in enumerate(RETENTION, start=1)
            ]
            return c.TableSet(
                id="retention",
                title="Data retention policy (NIWIS specification §23.3; the database records no retention metadata, "
                "so whether it is applied cannot be verified here)",
                columns=[
                    c.Column(key="dataClass", label="Data class", type="text"),
                    c.Column(key="covers", label="Covers", type="text"),
                    c.Column(key="retention", label="Retention", type="text"),
                    c.Column(key="location", label="Location", type="text"),
                ],
                rows=rows,
                row_key="id",
                total=len(rows),
                as_of=iso(f["newest"]),
            ).dump()
        if metric == "usage":
            raise unknown_501(NO_USAGE, "table:governance:usage")
        raise unknown("metric", metric, ["quality", "issues", "providers", "usage", "retention"])

    async def map(self, layer: str) -> dict[str, Any]:
        if layer == "providers":
            raise unknown_501(NO_PROVIDERS + "; no provider locations to map", "map:governance:providers")
        raise unknown("layer", layer, ["providers"])


def unknown_501(detail: str, missing: str) -> Exception:
    from app.core.errors import DataUnavailable

    return DataUnavailable(detail, missing=[missing])
