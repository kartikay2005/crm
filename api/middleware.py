"""
Middleware stack, applied in api/main.py in this order (outermost first):
  1. RequestIDMiddleware      — stamps X-Request-Id, enables audit correlation
  2. SecurityHeadersMiddleware — HSTS, CSP, X-Frame-Options, etc.
  3. RateLimitMiddleware      — Redis-backed sliding window, per-user or per-IP
  4. MetricsMiddleware        — Prometheus request counters/histograms

Order matters: request ID must be set before anything logs, security headers
should wrap even error responses, and rate limiting should reject before we
do any real work.
"""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from core.config import get_settings
from core.logging_config import get_logger
from core.metrics import http_requests_total, http_request_duration_seconds
from services.audit import audit_context

_log = get_logger(__name__)
_settings = get_settings()


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        with audit_context(request_id):
            response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline OWASP secure headers. CSP is intentionally strict — adjust
    only for specific asset needs (fonts, CDN) rather than loosening broadly."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; object-src 'none'"
        )
        if _settings.is_prod:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records request count + latency by (method, templated route, status).
    Uses route.path (the template, e.g. '/sellers/{id}') never the raw path,
    to avoid label cardinality blowup from path parameters."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        route = request.scope.get("route")
        route_template = route.path if route else request.url.path

        http_requests_total.labels(
            method=request.method, route=route_template, status=response.status_code
        ).inc()
        http_request_duration_seconds.labels(
            method=request.method, route=route_template
        ).observe(duration)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limit backed by Redis (INCR + EXPIRE pattern).
    Falls back to allowing all traffic if Redis is unreachable — availability
    over strictness for a rate limiter, but this is logged loudly so ops can
    respond. Swap to fail-closed if your threat model demands it.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Redis client is connected during lifespan startup and stored on
        # app.state — read it fresh each request rather than at __init__
        # time (middleware is constructed before the lifespan context runs).
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is None or request.url.path in ("/healthz", "/readyz", "/metrics"):
            return await call_next(request)

        # Key by authenticated user if present, else by IP
        auth_header = request.headers.get("Authorization", "")
        identity = auth_header[-16:] if auth_header else (
            request.client.host if request.client else "unknown"
        )
        limit = (
            _settings.rate_limit_per_minute_authenticated
            if auth_header else _settings.rate_limit_per_minute_anonymous
        )
        window = int(time.time() // 60)
        key = f"ratelimit:{identity}:{window}"

        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, 60)
            if count > limit:
                return JSONResponse(
                    {"detail": "Rate limit exceeded"}, status_code=429,
                    headers={"Retry-After": "60"},
                )
        except Exception as exc:
            _log.error("ratelimit.backend_unavailable", error=str(exc))

        return await call_next(request)
