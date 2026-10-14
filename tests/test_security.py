"""Security behaviour: no internal details in errors, scrubbed logs, injection-shaped input rejected, read-only DB."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.logging_config import JsonFormatter, scrub
from tests.conftest import requires_db


def test_scrub_removes_credentials() -> None:
    raw = (
        "connect postgresql://postgres.abc:s3cret@aws-1-eu-west-1.pooler.supabase.com:5432/postgres failed; "
        "key eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZSJ9.abcDEF123_- password=hunter2"
    )
    out = scrub(raw)
    assert "s3cret" not in out and "eyJhbGci" not in out and "hunter2" not in out
    assert "<db-url>" in out and "<jwt>" in out


def test_json_formatter_scrubs_messages_and_exceptions() -> None:
    try:
        raise RuntimeError("bad dsn postgresql://u:p@h/db")
    except RuntimeError:
        import sys

        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "dsn=%s", ("postgresql://u:p@h/db",), sys.exc_info())
    line = JsonFormatter().format(rec)
    assert "u:p@h" not in line


@pytest.mark.db
@requires_db
@pytest.mark.parametrize(
    ("path", "params", "status"),
    [
        ("/api/v1/rainfall/summary", {"municipality": "DC44'; DROP TABLE x; --"}, 400),
        ("/api/v1/rainfall/summary", {"province": "WC' OR '1'='1"}, 400),
        ("/api/v1/rainfall/timeseries", {"metric": 'monthly"; SELECT 1; --'}, 404),
        ("/api/v1/rainfall/map", {"layer": "../../etc/passwd"}, 404),
        ("/api/v1/rainfall/entities/DC44%27%3B--", {}, 404),
        ("/api/v1/rainfall/table", {"metric": "extremes", "limit": "100000"}, 400),
        ("/api/v1/rainfall/summary", {"from": "2020-01-01'; --"}, 400),
    ],
)
async def test_injection_shaped_input_is_rejected(
    client: httpx.AsyncClient, path: str, params: dict[str, str], status: int
) -> None:
    r = await client.get(path, params=params)
    assert r.status_code == status
    assert set(r.json()) <= {"error", "code", "issues", "detail", "missing"}


@pytest.mark.db
@requires_db
async def test_unexpected_error_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.rainfall.service import RainfallService

    async def boom(self: RainfallService) -> dict[str, Any]:
        raise RuntimeError("internal detail: SELECT * FROM secret_table WHERE password='x'")

    monkeypatch.setattr(RainfallService, "summary", boom)
    from app.main import create_app
    from tests.conftest import make_settings

    app = create_app(make_settings())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)  # behave like a real server
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/v1/rainfall/summary", params={"municipality": "DC1"})
        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "Internal server error" and "secret_table" not in r.text and body["requestId"]


@pytest.mark.db
@requires_db
async def test_only_get_is_allowed(client: httpx.AsyncClient) -> None:
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        r = await client.request(method, "/api/v1/rainfall/summary")
        assert r.status_code == 405
    pre = await client.options(
        "/api/v1/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"}
    )
    assert "DELETE" not in pre.headers.get("access-control-allow-methods", "")
