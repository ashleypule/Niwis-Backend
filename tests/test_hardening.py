"""Public-release hardening (Part 3): security headers, public cache headers, CORS, request limits, no stack traces,
trusted proxies, and the production read-only-role guard."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import make_settings, requires_db

PROVINCES = "/api/v1/reference/provinces"


async def client_with(peer: str = "203.0.113.7", **settings: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(make_settings(**settings))
    transport = httpx.ASGITransport(app=app, client=(peer, 50000), raise_app_exceptions=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


# ---------------------------------------------------------------------------------------------- settings / guard


def test_production_refuses_to_start_without_the_read_only_role() -> None:
    with pytest.raises(RuntimeError, match="DB_READONLY_ROLE_CONFIRMED"):
        create_app(make_settings(environment="production"))
    create_app(make_settings(environment="production", db_readonly_role_confirmed=True))  # starts
    create_app(make_settings(environment="development"))


def test_env_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DB_READONLY_ROLE_CONFIRMED", "true")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.is_production and s.db_readonly_role_confirmed
    s.check_production()


def test_cors_defaults_to_any_origin_and_follows_config() -> None:
    assert Settings(_env_file=None).cors_allow_origins == ["*"]  # type: ignore[call-arg]
    s = Settings(_env_file=None, cors_origins="https://niwis.dws.gov.za")  # type: ignore[call-arg]
    assert s.cors_allow_origins == ["https://niwis.dws.gov.za"]


# ---------------------------------------------------------------------------------------------- HTTP behaviour


@pytest.mark.db
@requires_db
async def test_security_headers_on_every_response() -> None:
    async for c in client_with():
        for path in (PROVINCES, "/api/v1/nope", "/docs"):
            h = (await c.get(path)).headers
            assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY", path
            assert h["referrer-policy"] == "no-referrer" and "frame-ancestors 'none'" in h["content-security-policy"]
            assert "strict-transport-security" not in h  # only in production
        assert "cdn.jsdelivr.net" in (await c.get("/docs")).headers["content-security-policy"]  # /docs stays public
    async for c in client_with(environment="production", db_readonly_role_confirmed=True):
        assert "max-age=" in (await c.get(PROVINCES)).headers["strict-transport-security"]


@pytest.mark.db
@requires_db
async def test_cache_headers_follow_the_server_cache() -> None:
    async for c in client_with(cache_ttl_summary_s=111, cache_ttl_map_s=222, cache_ttl_long_series_s=333):
        assert (await c.get(PROVINCES)).headers["cache-control"] == "public, max-age=3600"
        assert (await c.get("/api/v1/rainfall/summary")).headers["cache-control"] == "public, max-age=111"
        assert (await c.get("/api/v1/rainfall/map", params={"layer": "monthly"})).headers[
            "cache-control"
        ] == "public, max-age=222"
        r = await c.get("/api/v1/rainfall/timeseries", params={"metric": "monthly"})
        assert r.headers["cache-control"] == "public, max-age=333"
        assert (await c.get("/api/v1/health")).headers["cache-control"] == "no-store"
        assert (await c.get("/api/v1/rainfall/map", params={"layer": "nope"})).headers["cache-control"] == "no-store"


@pytest.mark.db
@requires_db
async def test_cors_allows_any_origin_for_get_without_credentials() -> None:
    async for c in client_with():
        r = await c.get(PROVINCES, headers={"Origin": "https://example.org"})
        assert r.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in r.headers
        pre = await c.options(
            PROVINCES, headers={"Origin": "https://example.org", "Access-Control-Request-Method": "DELETE"}
        )
        assert pre.status_code == 400  # GET, and POST for forecast runs, only
        pre = await c.options(
            "/api/v1/forecast/runs",
            headers={"Origin": "https://example.org", "Access-Control-Request-Method": "POST"},
        )
        assert pre.status_code == 200
    async for c in client_with(cors_origins=["https://niwis.example"]):
        r = await c.get(PROVINCES, headers={"Origin": "https://example.org"})
        assert "access-control-allow-origin" not in r.headers


@pytest.mark.db
@requires_db
async def test_request_size_limits_and_get_only() -> None:
    async for c in client_with(max_request_body_bytes=100, max_query_length=200):
        assert (await c.get(PROVINCES, params={"x": "a" * 300})).status_code == 414
        r = await c.request("GET", PROVINCES, content=b"x" * 500)
        assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE"
        assert (await c.post(PROVINCES, content=b"{}")).status_code == 405


@pytest.mark.db
@requires_db
async def test_no_stack_trace_reaches_the_client() -> None:
    app = create_app(make_settings())

    async def boom() -> None:
        raise ValueError("secret detail /app/internal.py line 42")

    app.add_api_route("/api/v1/boom", boom)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/v1/boom")
    assert r.status_code == 500 and r.json()["error"] == "Internal server error"
    assert "secret detail" not in r.text and "Traceback" not in r.text and "internal.py" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"


@pytest.mark.db
@requires_db
async def test_forwarded_for_only_from_configured_proxies() -> None:
    xff = {"X-Forwarded-For": "198.51.100.9"}
    # a peer that is not a configured proxy cannot choose its bucket with the header
    async for c in client_with(
        "203.0.113.7",
        rate_limit_per_minute=2,
        trust_proxy_headers=True,
        trusted_proxy_cidrs=["172.18.0.0/16"],
        rate_limit_trusted_cidrs=["198.51.100.0/24"],
    ):
        assert [(await c.get(PROVINCES, headers=xff)).status_code for _ in range(3)] == [200, 200, 429]
    # the configured proxy's header is believed, skipping proxies further right
    async for c in client_with(
        "172.18.0.2",
        rate_limit_per_minute=1,
        trust_proxy_headers=True,
        trusted_proxy_cidrs=["172.18.0.0/16"],
        rate_limit_trusted_cidrs=["198.51.100.0/24"],
    ):
        h = {"X-Forwarded-For": "198.51.100.9, 172.18.0.5"}
        assert [(await c.get(PROVINCES, headers=h)).status_code for _ in range(3)] == [200] * 3
