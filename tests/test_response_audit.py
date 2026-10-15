"""Response audit on the recorded fixture DB: every endpoint, key and selector value, in every fixture district and at
national scope, returns 200 or 501 and passes app.core.response_checks (fields listed in `unavailable` are null; no
value is dated outside its source tables' coverage). The response layer also asserts this on every request in tests
(`RESPONSE_CHECKS=raise`); this test makes sure every endpoint is actually requested.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.response_checks import coverage_violations, unavailable_violations
from app.modules import catalog
from app.modules.common import source_coverage
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]
SCOPES = [{}, {"municipality": "DC44"}, {"municipality": "CPT"}, {"municipality": "DC6"}, {"province": "WC"}]


async def test_every_endpoint_passes_the_response_checks(client: httpx.AsyncClient) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    coverage = await source_coverage(app.state.niwis)
    n, problems = await catalog.audit(client, SCOPES, coverage)
    assert n > 500, n  # the catalog really enumerated the API
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
    import datetime as dt

    body = {
        "categories": ["DJF"],
        "series": [{"key": "p0", "name": "1981–2000", "values": [21.58]}],
        "source": ["4_1_temperature_change_master"],
    }
    cov = {"4_1_temperature_change_master": (dt.date(2000, 1, 1), dt.date(2026, 9, 6))}
    assert coverage_violations(body, cov) == [
        "category series '1981–2000' reaches outside the source coverage 2000–2026"
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
        ("series.missing", {"series": []}, False),
    ],
)
def test_unavailable_field_paths(field: str, body: dict[str, object], ok: bool) -> None:
    assert (unavailable_violations({**body, "unavailable": [{"field": field, "reason": "r"}]}) == []) is ok
