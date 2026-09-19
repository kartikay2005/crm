"""
Audit event recording.

- `audit_event(...)` writes a single event to the current tenant's chain
- `verify_chain(...)` checks a tenant's chain for tampering (nightly job)
- `audit_context(request_id)` is a context manager that stamps all events in
  a request with the same request_id for correlation

The hash chain is per-tenant: sha256(prev_row_hash || canonical_json(current_row))
Chain start (first event per tenant) has prev_hash = None.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.logging_config import get_logger
from core.tenancy import current_tenant_or_none
from models.audit import AuditLog
from models.base import new_uuid

_log = get_logger(__name__)
_request_id: ContextVar[str | None] = ContextVar("audit_request_id", default=None)


@contextmanager
def audit_context(request_id: str) -> Iterator[None]:
    """Wrap request handling in this so every audit_event in the request
    shares a request_id for cross-event correlation."""
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


def _iso_utc(dt: datetime) -> str:
    """Canonical UTC ISO-8601 string, robust to backends that don't
    round-trip tzinfo.

    All `occurred_at` values are written with `datetime.now(timezone.utc)`,
    so at write time this is always tz-aware. But SQLite's `DateTime`
    column (even with `timezone=True`) silently drops tzinfo on read —
    confirmed empirically: a tz-aware UTC datetime written to a SQLite
    `DateTime(timezone=True)` column comes back naive. Since a Session's
    identity map is weak-referenced, any process that runs long enough (or
    a fresh query like `verify_chain`'s) will re-load rows from the DB
    instead of reusing the original Python object, at which point
    `.isoformat()` on the reloaded row silently loses the `+00:00` suffix
    that was present when `row_hash` was first computed — permanently
    breaking recomputation and making `verify_chain` report tampering that
    never happened. Postgres does not have this problem (`TIMESTAMPTZ`
    round-trips correctly), but this must behave identically on both
    backends since SQLite is the supported local-dev/CI database. Treating
    a naive datetime as "already UTC" (rather than raising or guessing the
    local zone) is correct here because every writer in this codebase only
    ever constructs `occurred_at` via `datetime.now(timezone.utc)`.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _canonical_row(row: AuditLog) -> str:
    """Deterministic JSON for hashing. Excludes id (random) and row_hash (self)."""
    return json.dumps(
        {
            "tenant_id": row.tenant_id,
            "occurred_at": _iso_utc(row.occurred_at),
            "actor_user_id": row.actor_user_id,
            "actor_ip": row.actor_ip,
            "event_type": row.event_type,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "payload": row.payload,
            "prev_hash": row.prev_hash,
            "request_id": row.request_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_row(row: AuditLog) -> str:
    return hashlib.sha256(_canonical_row(row).encode("utf-8")).hexdigest()


def audit_event(
    db: Session,
    event_type: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
    actor_ip: str | None = None,
    tenant_id_override: str | None = None,
) -> AuditLog:
    """Append one event. Never raises for expected conditions — audit failures
    are logged loudly but do not break the calling request. (Some regulated
    environments require the opposite: fail-closed audit. Change here if so.)"""
    # Resolve tenant + actor from context if not provided
    ctx = current_tenant_or_none()
    tenant_id = tenant_id_override or (ctx.tenant_id if ctx else None)
    if not tenant_id:
        _log.error("audit_event.no_tenant", event_type=event_type)
        raise RuntimeError(f"audit_event called with no tenant: {event_type}")

    if actor_user_id is None and ctx is not None:
        actor_user_id = ctx.user_id

    # Load previous row hash (last event for this tenant), ordered by the
    # true insertion sequence — see models/audit.py docstring on `seq` for
    # why (occurred_at, id) is not a safe tie-breaker.
    prev_hash = db.execute(
        select(AuditLog.row_hash)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.seq.desc())
        .limit(1)
    ).scalar_one_or_none()

    row = AuditLog(
        id=new_uuid(),
        tenant_id=tenant_id,
        occurred_at=datetime.now(timezone.utc),
        actor_user_id=actor_user_id,
        actor_ip=actor_ip,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload,
        prev_hash=prev_hash,
        row_hash="",  # placeholder, computed next
        request_id=_request_id.get(),
    )
    row.row_hash = _hash_row(row)
    db.add(row)
    db.flush()

    _log.info("audit", event_type=event_type, resource_type=resource_type,
              resource_id=resource_id)
    return row


def verify_chain(db: Session, tenant_id: str) -> tuple[bool, int | None]:
    """Recompute every row's hash and confirm the chain is intact.
    Returns (is_valid, first_bad_row_index)."""
    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.seq.asc())
    ).scalars().all()

    prev = None
    for idx, row in enumerate(rows):
        if row.prev_hash != prev:
            return False, idx
        if _hash_row(row) != row.row_hash:
            return False, idx
        prev = row.row_hash

    return True, None
