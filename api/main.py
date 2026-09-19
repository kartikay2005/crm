"""
Application entry point.

Run locally:  uvicorn api.main:app --reload
Run in prod:  gunicorn api.main:app -k uvicorn.workers.UvicornWorker -w 4 \
                  --bind 0.0.0.0:8000 --access-logfile - --error-logfile -

Startup sequence:
  1. Configure structured logging
  2. Install tenant query filter + insert guard on the sessionmaker
  3. Connect to Redis for rate limiting (optional — degrades gracefully)
  4. Register middleware (order matters — see api/middleware.py docstring)
  5. Register routers
  6. Register global exception handlers (never leak stack traces to clients)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.middleware import (
    MetricsMiddleware, RateLimitMiddleware, RequestIDMiddleware, SecurityHeadersMiddleware,
)
from api.routes import admin, auth, dataset_intelligence, feedback, health, prioritization, sellers
from api.routes.dataset_intelligence import models_router as dataset_models_router
from core.config import get_settings
from core.database import SessionLocal
from core.logging_config import configure_logging, get_logger
from core.tenancy import install_tenant_query_filter
from models.base import install_insert_guard

_settings = get_settings()

configure_logging()
_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    install_tenant_query_filter(SessionLocal)
    install_insert_guard(SessionLocal)

    redis_client = None
    if _settings.redis_url:
        try:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(_settings.redis_url, decode_responses=True)
            await redis_client.ping()
            _log.info("startup.redis_connected")
        except Exception as exc:
            _log.error("startup.redis_unavailable", error=str(exc))
            redis_client = None
    else:
        _log.warning("startup.redis_not_configured", note="rate limiting disabled")

    app.state.redis = redis_client
    _log.info("startup.complete", env=_settings.env, version=_settings.version)
    yield
    # --- Shutdown ---
    if redis_client:
        await redis_client.close()
    _log.info("shutdown.complete")


app = FastAPI(
    title=_settings.app_name,
    version=_settings.version,
    docs_url="/docs" if not _settings.is_prod else None,  # no public Swagger in prod
    redoc_url=None,
    lifespan=lifespan,
)

# --- Middleware (order: outermost registered LAST in Starlette) ------------
# Starlette applies middleware in reverse registration order, so we register
# from innermost-desired to outermost-desired.
app.add_middleware(MetricsMiddleware)
app.add_middleware(RateLimitMiddleware)  # reads redis client from app.state.redis per-request
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-Id", "X-Request-Id"],
)


# --- Global exception handlers ----------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return field-level errors but never raw exception internals."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation error", "errors": exc.errors()},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all. Logs full detail server-side, returns an opaque message to
    the client with the request ID so support can correlate without ever
    exposing internals (stack traces, SQL, file paths) to the caller."""
    request_id = getattr(request.state, "request_id", None)
    _log.error("unhandled_exception", error=str(exc), request_id=request_id, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "request_id": request_id},
    )


# --- Routes -------------------------------------------------------------
app.include_router(health.router)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(sellers.router, prefix="/api/v1")
app.include_router(prioritization.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(dataset_intelligence.router, prefix="/api/v1")
app.include_router(dataset_models_router, prefix="/api/v1")
