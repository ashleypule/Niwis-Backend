"""Shared plumbing for contract modules: global filters, selectors, scope weights, provenance, response caching and
the response checks (app.core.response_checks)."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Query, Request

from app.core.aggregate import aggregation_label, weights
from app.core.data import DataAccess
from app.core.errors import BadRequest, DataUnavailable, NotFound
from app.core.filters import Filters, parse_compare, parse_scope, parse_window
from app.core.provenance import is_stale, iso
from app.core.response_checks import Coverage, attach_reasons, invariant_violations, unexplained_null_violations
from app.core.thresholds import drought_class_or_none, drought_label
from app.schemas.contract import Scope, Status
from app.state import AppState, get_state

log = logging.getLogger("niwis.checks")


@dataclass(frozen=True)
class Selector:
    """A panel-level query parameter that picks what an endpoint returns (`set`, `var`, `kind`, `scale`, …).

    Services read selectors through `Ctx.select`, which validates the value: an unknown value is a 400 that lists the
    valid ones, never a silently ignored parameter. `multi` selectors take a comma-separated list.
    """

    name: str
    values: tuple[str, ...]
    default: str
    multi: bool = False


@dataclass
class Ctx:
    """Everything a module service needs for one request."""

    state: AppState
    filters: Filters
    newest_db: dt.date | None  # newest date anywhere in the DB (staleness reference)
    params: Mapping[str, str] = field(default_factory=dict)  # raw query parameters (for selectors)
    reasons: dict[str, str] = field(default_factory=dict)  # field path → why it may be null (see `explain`)

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

    def select(self, sel: Selector) -> str:
        """The validated value of a single-valued selector (its default when absent)."""
        raw = self.params.get(sel.name)
        value = sel.default if raw is None or raw == "" else raw.strip().lower()
        if value not in sel.values:
            raise BadRequest(
                f"Unknown {sel.name} {raw!r}",
                issues=[{"param": sel.name, "value": raw, "allowed": list(sel.values)}],
            )
        return value

    def select_many(self, sel: Selector) -> list[str]:
        """The validated, de-duplicated values of a comma-separated selector (its default when absent)."""
        raw = self.params.get(sel.name)
        items = [x.strip().lower() for x in (sel.default if raw is None else raw).split(",") if x.strip()]
        bad = [x for x in items if x not in sel.values]
        if bad or not items:
            raise BadRequest(
                f"Unknown {sel.name} value(s) {bad or [raw]!r}",
                issues=[{"param": sel.name, "value": raw, "invalid": bad, "allowed": list(sel.values)}],
            )
        return list(dict.fromkeys(items))

    def explain(self, field_path: str, reason: str) -> None:
        """Register why `field_path` (e.g. `series.trend`, `kpis.tempTrend`, `rows.spi6`) may be null. The response
        layer adds it to `unavailable` only if that field ends up entirely null (app.core.response_checks)."""
        self.reasons.setdefault(field_path, reason)

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
    request: Request,
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
    return Ctx(state, filters, newest, dict(request.query_params))


async def source_coverage(state: AppState) -> Coverage:
    """First and last record date of every table (from the cached table statistics)."""
    meta = await state.meta.sources()
    out = {}
    for it in meta["items"]:
        if it.get("firstDate") and it.get("asOf"):
            out[it["table"]] = (dt.date.fromisoformat(it["firstDate"]), dt.date.fromisoformat(it["asOf"]))
    return out


async def check_response(ctx: Ctx, path: str, body: Any) -> Any:
    """Attach the registered null reasons, then run app.core.response_checks on the body.

    `RESPONSE_CHECKS`: `log` (default) logs violations; `raise` (tests, debugging) fails the request on an invariant
    violation (a field listed in `unavailable` has values, or a value is dated outside its source tables' coverage) and
    logs completeness violations (an all-null element without a reason; enforced by the conformance audit, because on
    the 3-district test fixture national aggregates are legitimately null); `off` skips the checks.
    """
    attach_reasons(body, ctx.reasons)
    state = ctx.state
    mode = state.settings.response_checks
    if mode == "off" or not isinstance(body, dict):
        return body
    coverage = await source_coverage(state) if mode == "raise" else None
    invariants = invariant_violations(body, coverage)
    if invariants and mode == "raise":
        raise AssertionError(f"response check failed for {path}: " + "; ".join(invariants))
    violations = invariants + unexplained_null_violations(body)
    if violations:
        log.warning("response check failed", extra={"path": path, "violations": violations[:20]})
    return body


async def cached(request: Request, ctx: Ctx, ttl_s: int, build: Callable[[], Awaitable[Any]]) -> Any:
    """Cache a response body by path + sorted query string; bodies are checked once, before they are cached."""
    q = "&".join(f"{k}={v}" for k, v in sorted(request.query_params.multi_items()))

    async def checked() -> Any:
        return await check_response(ctx, request.url.path, await build())

    return await ctx.state.cache.get_or_set(f"resp:{request.url.path}?{q}", ttl_s, checked)


def drought_status(value: float | None) -> dict[str, Any]:
    """`status=` keyword for a drought-class pill; nothing when there is no value (never a "No drought" pill)."""
    cls = drought_class_or_none(value)
    return {} if cls is None else {"status": Status(scale="drought", id=cls, label=drought_label(cls))}


def anomaly_split_reasons(ctx: Ctx, values: list[float | None], what: str = "anomaly") -> None:
    """Reasons for the empty half of a positive / negative bar split (`series.pos`, `series.neg`)."""
    have = [v for v in values if v is not None]
    if not have:
        for key in ("pos", "neg"):
            ctx.explain(f"series.{key}", f"No {what} could be computed for this scope and period")
        return
    ctx.explain("series.pos", f"No month in this period had a positive {what} (all {len(have)} were below normal)")
    ctx.explain(
        "series.neg", f"No month in this period had a negative {what} (all {len(have)} were at or above normal)"
    )


def unknown(kind: str, key: str | None, allowed: list[str]) -> NotFound:
    return NotFound(f"Unknown {kind} {key!r}", issues=[{"param": kind, "allowed": sorted(allowed)}])


def forecast_unavailable(module: str, metric: str) -> DataUnavailable:
    return DataUnavailable(
        "Forecasts are not available yet: no forecast models or forecast tables exist in the database.",
        missing=[f"forecast:{module}:{metric}"],
    )
