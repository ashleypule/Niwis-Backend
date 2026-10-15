"""Response audit on the recorded fixture DB: every endpoint, key and selector value returns 200 or 501 and passes
app.core.response_checks.

- Invariants (fields listed in `unavailable` are null; no value dated outside its source tables' coverage) in every
  scope, national and province included. The response layer also asserts these on every request in tests.
- Completeness (every all-null series / KPI / column / map says why, BACKEND_ISSUES #5) in the fixture's three
  districts. The fixture holds 3 of 52 districts, so national and province aggregates are null by the 95 % area rule;
  completeness at those scopes is checked on the real database (tests/integration/test_response_audit_live.py).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest

from app.core.response_checks import (
    attach_reasons,
    coverage_violations,
    unavailable_violations,
    unexplained_null_violations,
)
from app.modules import catalog
from app.modules.common import source_coverage
from tests.conftest import requires_db

DISTRICTS = [{"municipality": "DC44"}, {"municipality": "CPT"}, {"municipality": "DC6"}]
AGGREGATES = [{}, {"province": "WC"}]


@pytest.mark.db
@requires_db
async def test_every_endpoint_passes_the_invariants(client: httpx.AsyncClient) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    coverage = await source_coverage(app.state.niwis)
    n, problems = await catalog.audit(client, AGGREGATES, coverage, completeness=False)
    assert n > 300, n  # the catalog really enumerated the API
    assert not problems, "\n".join(problems[:40])


@pytest.mark.db
@requires_db
async def test_every_endpoint_passes_all_checks_in_the_fixture_districts(client: httpx.AsyncClient) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    coverage = await source_coverage(app.state.niwis)
    n, problems = await catalog.audit(client, DISTRICTS, coverage)
    assert n > 500, n
    assert not problems, "\n".join(problems[:40])


def test_checks_catch_a_value_listed_as_unavailable() -> None:
    body = {
        "series": [{"key": "p0", "name": "1981–2000", "values": [21.58, 16.81, 12.66, 17.88]}],
        "categories": ["DJF", "MAM", "JJA", "SON"],
        "unavailable": [{"field": "series.p0", "reason": "No data before 2000 in the database"}],
    }
    assert unavailable_violations(body) == [
        "'series.p0' is listed as unavailable but has values [21.58, 16.81, 12.66, 17.88]"
    ]


def test_checks_catch_a_period_before_the_database_starts() -> None:
    body = {
        "categories": ["DJF"],
        "series": [{"key": "p0", "name": "1981–2000", "values": [21.58]}],
        "source": ["4_1_temperature_change_master"],
    }
    cov = {"4_1_temperature_change_master": (dt.date(2000, 1, 1), dt.date(2026, 9, 6))}
    assert coverage_violations(body, cov) == [
        "category series '1981–2000' reaches outside the source coverage 2000–2026"
    ]


def test_checks_catch_a_value_after_the_last_record() -> None:
    body = {
        "xType": "time",
        "series": [{"key": "s", "data": [["2025-12-01", 1.0], ["2026-01-01", 2.0]]}],
        "source": ["satellite_remote_sensing"],
    }
    cov = {"satellite_remote_sensing": (dt.date(2000, 1, 1), dt.date(2025, 12, 1))}
    assert coverage_violations(body, cov) == [
        "series 's' value dated 2026-01-01 is outside the source coverage 2000-01-01 – 2025-12-01"
    ]


@pytest.mark.parametrize(
    ("field", "body", "ok"),
    [
        ("kpis.x", {"kpis": [{"id": "x", "value": None}]}, True),
        ("kpis.x", {"kpis": [{"id": "x", "value": 1.0}]}, False),
        ("series.s", {"series": [{"key": "s", "data": [["2020-01-01", None], ["2020-02-01", 2.0]]}]}, False),
        ("series.b", {"series": [{"key": "b", "data": [["2020-01-01", None, None]]}]}, True),
        ("rows.c", {"columns": [{"key": "c", "type": "number"}], "rows": [{"c": None}, {"c": None}]}, True),
        ("rows.r1", {"columns": [{"key": "v", "type": "number"}], "rows": [{"id": "r1", "v": 3.0}]}, False),
        ("rows.r1.t", {"columns": [{"key": "t", "type": "text"}], "rows": [{"id": "r1", "t": None}]}, True),
        ("values", {"values": {"DC1": None, "DC2": None}}, True),
        ("values", {"values": [[None, 1.0]]}, False),
        ("series.missing", {"series": []}, False),
    ],
)
def test_unavailable_field_paths(field: str, body: dict[str, object], ok: bool) -> None:
    assert (unavailable_violations({**body, "unavailable": [{"field": field, "reason": "r"}]}) == []) is ok


def test_checks_catch_an_all_null_series_without_a_reason() -> None:
    body: dict[str, Any] = {
        "series": [{"key": "drought", "data": [["2020-01-01", None], ["2021-01-01", None]]}],
        "kpis": [{"id": "k", "value": None}],
    }
    assert unexplained_null_violations(body) == [
        "KPI 'k' is null without an unavailable entry",
        "series 'drought' is entirely null without an unavailable entry",
    ]
    attach_reasons(body, {"series.drought": "zero base", "kpis.k": "why", "series.other": "unused"})
    assert unexplained_null_violations(body) == []
    assert body["kpis"][0]["unavailableReason"] == "why"
    assert [u["field"] for u in body["unavailable"]] == ["series.drought", "kpis.k"]  # nothing for a missing field


def test_reasons_are_not_attached_to_values() -> None:
    body: dict[str, Any] = {"series": [{"key": "s", "data": [["2020-01-01", None], ["2021-01-01", 1.0]]}]}
    attach_reasons(body, {"series.s": "only some years"})
    assert "unavailable" not in body
