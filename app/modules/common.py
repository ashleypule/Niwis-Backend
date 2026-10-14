"""Shared plumbing for contract modules: global filters, scope weights, provenance and response caching."""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Query, Request

from app.core.aggregate import aggregation_label, weights
from app.core.data import DataAccess
from app.core.errors import DataUnavailable, NotFound
from app.core.filters import Filters, parse_compare, parse_scope, parse_window
from app.core.provenance import is_stale, iso
from app.schemas.contract import Scope
from app.state import AppState, get_state


@dataclass
class Ctx:
    """Everything a module service needs for one request."""

    state: AppState
    filters: Filters
    newest_db: dt.date | None  # newest date anywhere in the DB (staleness reference)

    @property
    def data(self) -> DataAccess:
        return self.state.data

    @property
    def scope(self) -> Scope:
        s = self.filters.scope
        level = "municipality" if s.level == "municipality" else s.level
        return Scope(level=level, id=s.id, name=s.name)

    @property
    def aggregation(self) -> str:
        return aggregation_label(self.filters.scope)

    def weights(self) -> dict[str, float]:
        return weights(self.filters.scope, self.state.spatial)

    def stale(self, as_of: dt.date | None) -> bool:
        return is_stale(as_of, self.newest_db, self.state.settings.stale_after_days)

    def provenance(self, as_of: dt.date | None, sources: list[str], *, baseline: bool = False) -> dict[str, Any]:
        """Top-level provenance fields (additions, see docs/CONTRACT_CHANGES.md §4)."""
        out: dict[str, Any] = {
            "asOf": iso(as_of),
            "source": sorted(set(sources)),
            "isStale": self.stale(as_of),
            "aggregation": self.aggregation,
        }
        if baseline:
            b = self.state.registry.baseline
            out["baseline"] = b.stored_anomalies
            out["baselineNote"] = b.note
        return out


async def get_ctx(
    state: AppState = Depends(get_state),
    province: str | None = Query(None, description="Province code: EC FS GP KZN LP MP NC NW WC"),
    wma: str | None = Query(None, description="WMA1 … WMA9 (district-overlap aggregate, approximate)"),
    catchment: str | None = Query(None, description="Not available: returns 501 DATA_UNAVAILABLE"),
    municipality: str | None = Query(None, description="District / metro MDB code, e.g. DC44, CPT"),
    period: str | None = Query(None, description="latest 1M 3M 6M 12M 24M 5Y 10Y historical (default 12M)"),
    date_from: str | None = Query(None, alias="from", description="YYYY-MM-DD (overrides period)"),
    date_to: str | None = Query(None, alias="to", description="YYYY-MM-DD"),
    compare: str | None = Query(None, description="1 = include comparison series"),
    entity_id: str | None = Query(None, alias="entityId", description="District MDB code for entity datasets"),
) -> Ctx:
    scope = parse_scope(province, wma, catchment, municipality, wma_districts=state.spatial.wma_districts())
    filters = Filters(scope, parse_window(period, date_from, date_to), parse_compare(compare), entity_id)
    newest = await state.meta.newest_date() if state.db.configured else None
    return Ctx(state, filters, newest)


async def cached(request: Request, state: AppState, ttl_s: int, build: Callable[[], Awaitable[Any]]) -> Any:
    """Cache a response body by path + sorted query string."""
    q = "&".join(f"{k}={v}" for k, v in sorted(request.query_params.multi_items()))
    return await state.cache.get_or_set(f"resp:{request.url.path}?{q}", ttl_s, build)


def unknown(kind: str, key: str | None, allowed: list[str]) -> NotFound:
    return NotFound(f"Unknown {kind} {key!r}", issues=[{"param": kind, "allowed": sorted(allowed)}])


def forecast_unavailable(module: str, metric: str) -> DataUnavailable:
    return DataUnavailable(
        "Forecasts are not available yet: no forecast models or forecast tables exist in the database.",
        missing=[f"forecast:{module}:{metric}"],
    )
