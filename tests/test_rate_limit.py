"""Rate limiting (BACKEND_ISSUES #4): 600 / min per client IP by default, health checks exempt, trusted networks
exempt, and `X-Forwarded-For` ignored unless TRUST_PROXY_HEADERS is set (otherwise clients could spoof their IP)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from tests.conftest import make_settings, requires_db

pytestmark = [pytest.mark.db, requires_db]
PATH = "/api/v1/reference/provinces"


async def peer_client(peer: str = "203.0.113.7", **settings: Any) -> AsyncIterator[httpx.AsyncClient]:
    """A client whose socket address is `peer` (what the API sees without proxy headers)."""
    app = create_app(make_settings(**settings))
    transport = httpx.ASGITransport(app=app, client=(peer, 50000))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def codes(c: httpx.AsyncClient, n: int, path: str = PATH, **headers: str) -> list[int]:
    return [(await c.get(path, headers=headers)).status_code for _ in range(n)]


def test_default_limit_is_600_and_configurable() -> None:
    assert Settings(_env_file=None).rate_limit_per_minute == 600  # type: ignore[call-arg]
    assert Settings(_env_file=None, rate_limit_per_minute=50).rate_limit_per_minute == 50  # type: ignore[call-arg]
    s = Settings(_env_file=None, rate_limit_trusted_cidrs="10.0.0.0/8, 196.21.0.0/16")  # type: ignore[call-arg]
    assert s.rate_limit_trusted_cidrs == ["10.0.0.0/8", "196.21.0.0/16"]
    assert Settings(_env_file=None).trust_proxy_headers is False  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_trusted_cidrs="10.0.0.0/33")  # type: ignore[call-arg]


async def test_limit_applies_per_ip() -> None:
    async for c in peer_client(rate_limit_per_minute=3):
        assert await codes(c, 4) == [200, 200, 200, 429]
        r = await c.get(PATH)
        assert r.json() == {"error": "Too many requests", "code": "RATE_LIMITED"}
        assert 1 <= int(r.headers["retry-after"]) <= 60


async def test_health_is_exempt() -> None:
    async for c in peer_client(rate_limit_per_minute=2):
        assert await codes(c, 2) == [200, 200]
        assert await codes(c, 5, "/api/v1/health") == [200] * 5  # Docker polls this every 30 s
        assert await codes(c, 1) == [429]  # other endpoints are still limited


async def test_trusted_networks_are_not_limited() -> None:
    async for c in peer_client("10.20.30.40", rate_limit_per_minute=2, rate_limit_trusted_cidrs=["10.0.0.0/8"]):
        assert await codes(c, 5) == [200] * 5
    async for c in peer_client("11.20.30.40", rate_limit_per_minute=2, rate_limit_trusted_cidrs=["10.0.0.0/8"]):
        assert await codes(c, 3) == [200, 200, 429]


async def test_forwarded_for_is_ignored_by_default() -> None:
    """Without TRUST_PROXY_HEADERS a client cannot dodge the limit (or claim a trusted address) via the header."""
    async for c in peer_client(rate_limit_per_minute=2, rate_limit_trusted_cidrs=["10.0.0.0/8"]):
        got = [(await c.get(PATH, headers={"X-Forwarded-For": f"198.51.100.{i}"})).status_code for i in range(3)]
        assert got == [200, 200, 429]
        assert await codes(c, 1, **{"X-Forwarded-For": "10.0.0.1"}) == [429]


async def test_forwarded_for_behind_a_trusted_proxy() -> None:
    """With TRUST_PROXY_HEADERS the right-most entry (set by the proxy) is the client; entries left of it are
    client-supplied and do not change the bucket."""
    async for c in peer_client("172.18.0.2", rate_limit_per_minute=2, trust_proxy_headers=True):
        a = {"X-Forwarded-For": "198.51.100.1"}
        b = {"X-Forwarded-For": "198.51.100.2"}
        assert await codes(c, 3, **a) == [200, 200, 429]
        assert await codes(c, 2, **b) == [200, 200]  # another client behind the same proxy has its own budget
        spoof = {"X-Forwarded-For": "1.2.3.4, 198.51.100.1"}
        assert await codes(c, 1, **spoof) == [429]  # prepending an address does not reset the budget
    async for c in peer_client(
        "172.18.0.2", rate_limit_per_minute=1, trust_proxy_headers=True, rate_limit_trusted_cidrs=["196.21.0.0/16"]
    ):
        assert await codes(c, 3, **{"X-Forwarded-For": "196.21.4.5"}) == [200] * 3  # the DWS network via the proxy
