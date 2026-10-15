"""Request logging (with request id) and per-client rate limiting."""

from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

log = logging.getLogger("niwis.http")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
EXEMPT_PATHS = frozenset({"/api/v1/health"})  # polled by Docker every 30 s


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        )
        return response


def parse_networks(cidrs: Iterable[str]) -> list[Network]:
    """`10.0.0.0/8`, `196.21.0.0/16`, single addresses; raises ValueError on a malformed entry (refuse to start)."""
    return [ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip()]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding one-minute window per client. In-process only; use the gateway / Redis when scaled out.

    - `/api/v1/health` is never limited (container health checks).
    - Requests from `trusted` networks (`RATE_LIMIT_TRUSTED_CIDRS`, e.g. the DWS network) are not limited.
    - The client IP is the socket peer. `X-Forwarded-For` is used **only** with `trust_proxy_headers`
      (`TRUST_PROXY_HEADERS=true`, when the API sits behind a reverse proxy that sets it); otherwise any client could
      claim another IP, or a trusted one, to dodge the limit. With it, the right-most entry (the address the nearest
      proxy saw) is used; entries further left are client-supplied and ignored.
    - `client_key` is the hook for authentication: once tokens exist, return a per-user key there (e.g.
      `f"user:{request.state.user_id}"`) so colleagues behind one office NAT get separate budgets.
    """

    def __init__(
        self,
        app: ASGIApp,
        per_minute: int,
        trusted: Iterable[str] = (),
        trust_proxy_headers: bool = False,
    ) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self.trusted = parse_networks(trusted)
        self.trust_proxy_headers = trust_proxy_headers
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def client_ip(self, request: Request) -> str:
        if self.trust_proxy_headers:
            forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
            if forwarded:
                return forwarded[-1]
        return request.client.host if request.client else "unknown"

    def is_trusted(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.trusted)

    def client_key(self, request: Request, ip: str) -> str:
        """The bucket a request counts against (authentication hook: key by user / token here once it exists)."""
        return f"ip:{ip}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if self.per_minute <= 0 or not path.startswith("/api/") or path in EXEMPT_PATHS:
            return await call_next(request)
        ip = self.client_ip(request)
        if self.is_trusted(ip):
            return await call_next(request)
        key = self.client_key(request, ip)
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            retry = max(1, int(60 - (now - q[0])))
            return JSONResponse(
                {"error": "Too many requests", "code": "RATE_LIMITED"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        q.append(now)
        if len(self.hits) > 10_000:  # bound memory: forget clients idle for over a minute
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                del self.hits[k]
        return await call_next(request)
