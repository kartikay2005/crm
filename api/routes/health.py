"""
Liveness/readiness probes + Prometheus scrape endpoint.

- /healthz — liveness: is the process alive and responsive? No dependency checks.
- /readyz  — readiness: can the process serve traffic? Checks DB connectivity.
- /metrics — Prometheus exposition format.

These are intentionally unauthenticated (k8s probes and Prometheus scrapers
don't carry bearer tokens) — keep them free of sensitive data.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import HealthResponse
from core.config import get_settings
from core.database import check_database_health
from services.demo_seed import run_seed_demo

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


@router.post("/_seed-demo")
def seed_demo_endpoint(token: str = Query(...), db: Session = Depends(get_db)) -> dict:
    """Guarded by SEED_TOKEN (unset = always 404 — see core/config.py).
    Runs in-process deliberately: see services/demo_seed.py's docstring
    for why this must not run as a separate process alongside gunicorn."""
    settings = get_settings()
    expected = settings.seed_token.get_secret_value() if settings.seed_token else None
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    result = run_seed_demo(db)
    if result["already_seeded"]:
        return {"status": "already seeded", "tenant_id": result["tenant_id"]}
    return {
        "status": "seeded",
        "login": {
            "workspace": result["tenant_slug"],
            "email": result["email"],
            "password": result["password"],
        },
        "datasets": result["datasets"],
    }
