"""
Liveness/readiness probes + Prometheus scrape endpoint.

- /healthz — liveness: is the process alive and responsive? No dependency checks.
- /readyz  — readiness: can the process serve traffic? Checks DB connectivity.
- /metrics — Prometheus exposition format.

These are intentionally unauthenticated (k8s probes and Prometheus scrapers
don't carry bearer tokens) — keep them free of sensitive data.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.schemas import HealthResponse
from core.config import get_settings
from core.database import check_database_health

router = APIRouter(tags=["health"])
_settings = get_settings()


@router.get("/healthz", response_model=HealthResponse)
def liveness():
    return HealthResponse(
        status="ok", version=_settings.version, timestamp=datetime.now(timezone.utc)
    )


@router.get("/readyz")
def readiness(response: Response):
    db_ok = check_database_health()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "database": "down"}
    return {"status": "ready", "database": "up"}


@router.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
