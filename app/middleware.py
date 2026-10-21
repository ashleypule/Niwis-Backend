"""HTTP middleware: request logging (with request id), per-client rate limiting, request size limits, security
headers and public cache headers. The API is public (no login) and read-only (GET only)."""

from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterable

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


def in_networks(ip: str, networks: list[Network]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding one-minute window per client. In-process only; use the gateway / Redis when scaled out.

    - `/api/v1/health` is never limited (container health checks).
    - Requests from `trusted` networks (`RATE_LIMIT_TRUSTED_CIDRS`, e.g. the DWS network) are not limited.
    - The client IP is the socket peer. `X-Forwarded-For` is used **only** with `trust_proxy_headers`
      (`TRUST_PROXY_HEADERS=true`, when the API sits behind a reverse proxy that sets it) and, when `proxies`
      (`TRUSTED_PROXY_CIDRS`) is set, only for requests whose socket peer is one of those proxies; otherwise any
      client could claim another IP, or a trusted one, to dodge the limit. The header is read from the right, skipping
      the configured proxies: the first other entry is the address the nearest proxy saw; entries further left are
      client-supplied and ignored.
    - `client_key` is the hook for authentication: once tokens exist, return a per-user key there (e.g.
      `f"user:{request.state.user_id}"`) so colleagues behind one office NAT get separate budgets.
    """

    def __init__(
        self,
        app: ASGIApp,
        per_minute: int,
        trusted: Iterable[str] = (),
        trust_proxy_headers: bool = False,
        proxies: Iterable[str] = (),
    ) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self.trusted = parse_networks(trusted)
        self.trust_proxy_headers = trust_proxy_headers
        self.proxies = parse_networks(proxies)
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if not self.trust_proxy_headers or (self.proxies and not in_networks(peer, self.proxies)):
            return peer
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        for hop in reversed(forwarded):
            if not (self.proxies and in_networks(hop, self.proxies)):
                return hop
        return peer

    def is_trusted(self, ip: str) -> bool:
        return in_networks(ip, self.trusted)

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


# ---------------------------------------------------------------------------------------------------- hardening

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
# Swagger UI (/docs) loads its script and stylesheet from jsDelivr and the FastAPI favicon; nothing else
DOCS_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect"})


def security_headers(path: str, hsts: bool) -> dict[str, str]:
    h = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        # the data are public and the frontend may be served from another origin
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Content-Security-Policy": DOCS_CSP if path in DOCS_PATHS else API_CSP,
    }
    if hsts:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return h


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Security headers on every response. HSTS only when `hsts` (production, served over HTTPS by the proxy)."""

    def __init__(self, app: ASGIApp, hsts: bool = False) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.update(security_headers(request.url.path, self.hsts))
        return response


class CacheControlMiddleware(BaseHTTPMiddleware):
    """`Cache-Control: public, max-age=<the server cache TTL>` on successful GETs (and on 501 DATA_UNAVAILABLE, which
    changes only when the data do). Everything is public: there is no login and no per-user data. Paths without a TTL
    (health) and errors are `no-store`."""

    def __init__(self, app: ASGIApp, ttl_for: Callable[[str], int | None]) -> None:
        super().__init__(app)
        self.ttl_for = ttl_for

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if "cache-control" in response.headers:
            return response
        ttl = self.ttl_for(request.url.path) if request.method in ("GET", "HEAD") else None
        if ttl and response.status_code in (200, 501):
            response.headers["Cache-Control"] = f"public, max-age={ttl}"
        elif request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


class RequestLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests before routing: a body larger than `max_body` (the API takes no bodies: every
    endpoint is a GET) or a chunked body → 413; a query string longer than `max_query` → 414."""

    def __init__(self, app: ASGIApp, max_body: int, max_query: int) -> None:
        super().__init__(app)
        self.max_body = max_body
        self.max_query = max_query

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if len(request.url.query) > self.max_query:
            return JSONResponse({"error": "Query string too long", "code": "URI_TOO_LONG"}, status_code=414)
        length = request.headers.get("content-length")
        chunked = "chunked" in request.headers.get("transfer-encoding", "").lower()
        try:
            too_big = chunked or (length is not None and int(length) > self.max_body)
        except ValueError:
            too_big = True
        if too_big:
            return JSONResponse({"error": "Request body too large", "code": "PAYLOAD_TOO_LARGE"}, status_code=413)
        return await call_next(request)
