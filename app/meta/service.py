"""Table statistics for /meta and /health: row counts, date coverage, completeness, freshness.

All numbers come from the database (cached for `cache_ttl_meta_s`). Completeness = rows / expected rows, where
expected = 52 districts × number of periods between the first and last date at the table's resolution.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.core.cache import Cache
from app.core.districts import districts
from app.core.provenance import is_stale, iso
from app.db import Database, ident
from app.registry.registry import Dataset, Registry

log = logging.getLogger("niwis.meta")


@dataclass
class TableStats:
    table: str
    module: str
    db_module: int
    resolution: str
    exists: bool
    rows: int | None = None
    locations: int | None = None
    first_date: dt.date | None = None
    last_date: dt.date | None = None  # stamp of the newest record (1st of month/year for monthly/annual tables)
    covered_to: dt.date | None = None  # last day the newest record covers
    completeness_pct: float | None = None
    size_bytes: int | None = None
    stats_updated_at: dt.datetime | None = None
    error: str | None = None


def _periods(first: dt.date, last: dt.date, resolution: str) -> int:
    if resolution == "daily":
        return (last - first).days + 1
    if resolution == "monthly":
        return (last.year - first.year) * 12 + last.month - first.month + 1
    if resolution == "annual":
        return last.year - first.year + 1
    return (last - first).days // 10 + 1  # dekadal


class MetaService:
    def __init__(self, db: Database, registry: Registry, cache: Cache, settings: Settings) -> None:
        self.db, self.registry, self.cache, self.settings = db, registry, cache, settings

    async def _one(self, ds: Dataset) -> TableStats:
        st = TableStats(ds.table, ds.module, ds.db_module, ds.time.resolution, exists=False)
        try:
            reg = await self.db.fetchval("SELECT to_regclass($1)::text", ds.fq)
            if reg is None:
                st.error = "table not found"
                return st
            st.exists = True
            d, end, loc = ds.date_sql(), ds.period_end_sql(), ident(ds.location.column)
            row = await self.db.fetchrow(
                f"SELECT count(*) AS n, count(DISTINCT {loc}) AS locs, min({d}) AS first, max({d}) AS last, "
                f"max({end}) AS covered_to FROM {ds.fq}"
            )
            assert row is not None
            st.rows, st.locations = int(row["n"]), int(row["locs"])
            st.first_date, st.last_date, st.covered_to = row["first"], row["last"], row["covered_to"]
            if st.first_date and st.last_date:
                expected = len(districts()) * _periods(st.first_date, st.last_date, ds.time.resolution)
                st.completeness_pct = round(min(100.0, 100.0 * st.rows / expected), 2) if expected else None
            cat = await self.db.fetchrow(
                "SELECT pg_total_relation_size(to_regclass($1)) AS size, "
                "greatest(s.last_analyze, s.last_autoanalyze) AS analysed "
                "FROM pg_stat_user_tables s WHERE s.relid = to_regclass($1)",
                ds.fq,
            )
            if cat is not None:
                st.size_bytes, st.stats_updated_at = cat["size"], cat["analysed"]
        except Exception as exc:
            log.exception("table stats failed", extra={"table": ds.table})
            st.error = type(exc).__name__
        return st

    async def table_stats(self) -> list[TableStats]:
        async def load() -> list[TableStats]:
            sem = asyncio.Semaphore(max(1, self.settings.db_pool_max - 1))

            async def guarded(ds: Dataset) -> TableStats:
                async with sem:
                    return await self._one(ds)

            return list(await asyncio.gather(*(guarded(ds) for ds in self.registry.datasets.values())))

        stats: list[TableStats] = await self.cache.get_or_set("meta:table_stats", self.settings.cache_ttl_meta_s, load)
        return stats

    async def newest_date(self) -> dt.date | None:
        """Newest daily record in the database — the reference point for staleness.

        Monthly / annual rows cover their whole period, so only daily tables define "newest". A cheap max() per daily
        table, cached separately from the full statistics so the first data request does not wait for them.
        """

        async def load() -> dt.date | None:
            daily = [ds for ds in self.registry.datasets.values() if ds.time.resolution == "daily"]

            async def one(ds: Dataset) -> dt.date | None:
                try:
                    v = await self.db.fetchval(f"SELECT max({ds.date_sql()}) FROM {ds.fq}")
                    return v if isinstance(v, dt.date) else None
                except Exception:
                    log.exception("newest date failed", extra={"table": ds.table})
                    return None

            found = [d for d in await asyncio.gather(*(one(ds) for ds in daily)) if d]
            return max(found) if found else None

        newest: dt.date | None = await self.cache.get_or_set("meta:newest", self.settings.cache_ttl_meta_s, load)
        return newest

    # ---------------------------------------------------------------- payloads

    async def sources(self) -> dict[str, Any]:
        newest = await self.newest_date()
        items = []
        for st in await self.table_stats():
            ds = self.registry.datasets[st.table]
            covered = min(st.covered_to, newest) if st.covered_to and newest else st.covered_to
            cols = ds.columns
            items.append(
                {
                    "table": st.table,
                    "module": st.module,
                    "dbModule": st.db_module,
                    "resolution": st.resolution,
                    "exists": st.exists,
                    "rows": st.rows,
                    "districts": st.locations,
                    "firstDate": iso(st.first_date),
                    "asOf": iso(st.last_date),
                    "coveredTo": iso(covered),
                    "isStale": is_stale(covered, newest, self.settings.stale_after_days),
                    "completenessPct": st.completeness_pct,
                    "sizeBytes": st.size_bytes,
                    "statsUpdatedAt": st.stats_updated_at.isoformat() if st.stats_updated_at else None,
                    "variables": {
                        "canonical": sorted({c.var for c in cols.values() if c.role == "canonical"}),
                        "duplicate": sorted(n for n, c in cols.items() if c.role == "duplicate"),
                        "withheld": [
                            {"column": n, "reason": c.reason} for n, c in cols.items() if c.role == "withheld"
                        ],
                        "unused": sorted(n for n, c in cols.items() if c.role == "unused"),
                    },
                    "knownIssues": ds.known_issues,
                    "error": st.error,
                }
            )
        return {
            "newestDataDate": iso(newest),
            "staleAfterDays": self.settings.stale_after_days,
            "note": "statsUpdatedAt is when Postgres last analysed the table (a proxy; tables have no load timestamp).",
            "items": items,
        }

    async def coverage(self) -> dict[str, Any]:
        newest = await self.newest_date()
        stats = await self.table_stats()
        modules = []
        for module in self.registry.modules:
            served_tables = {ds.table for ds in self.registry.datasets_for_module(module)}
            mine = [s for s in stats if s.table in served_tables]
            rows = sum(s.rows or 0 for s in mine)
            weighted = [(s.completeness_pct, s.rows) for s in mine if s.completeness_pct is not None and s.rows]
            covered = [min(s.covered_to, newest) if newest else s.covered_to for s in mine if s.covered_to]
            served = {
                c.var
                for ds in self.registry.datasets_for_module(module)
                for c in ds.columns.values()
                if c.role == "canonical"
            }
            withheld = {
                c.var
                for ds in self.registry.datasets_for_module(module)
                for c in ds.columns.values()
                if c.role == "withheld"
            } - served
            modules.append(
                {
                    "module": module,
                    "tables": [s.table for s in mine],
                    "rows": rows,
                    "firstDate": iso(min((s.first_date for s in mine if s.first_date), default=None)),
                    "asOf": iso(max((s.last_date for s in mine if s.last_date), default=None)),
                    "oldestCoveredTo": iso(min(covered, default=None)),
                    "isStale": any(is_stale(c, newest, self.settings.stale_after_days) for c in covered),
                    "completenessPct": round(sum(p * r for p, r in weighted) / sum(r for _, r in weighted), 2)
                    if weighted
                    else None,
                    "variables": {"live": len(served), "withheld": len(withheld)},
                }
            )
        return {"newestDataDate": iso(newest), "staleAfterDays": self.settings.stale_after_days, "modules": modules}

    def variables(self) -> dict[str, Any]:
        """Every registered variable with its canonical source, or why it is unavailable."""
        items = []
        dups = self.registry.duplicates()
        for var, meta in self.registry.variables.items():
            src = self.registry.canonical_source(var)
            items.append(
                {
                    "id": var,
                    "label": meta.label,
                    "unit": meta.unit,
                    "metric": meta.metric,
                    "status": "live" if src else "unavailable",
                    "source": f"{src[0].table}.{src[1]}" if src else None,
                    "duplicates": dups.get(var, {}).get("duplicates", []),
                    "withheld": self.registry.withheld_reasons(var),
                }
            )
        return {"items": items}
