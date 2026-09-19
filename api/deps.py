"""
FastAPI dependencies: current user extraction, tenant context establishment,
permission enforcement.

Flow per request:
  1. `get_db` opens a session-scoped transaction
  2. `get_current_claims` decodes + validates the JWT
  3. `get_tenant_context` establishes the ContextVar for the request duration
     (this is what makes core.tenancy's query filter work)
  4. `require_permission(perm)` is a dependency factory routes use to gate access

*** Why get_tenant_context is `async def`, not `def` — this matters ***
FastAPI dispatches every synchronous (`def`, non-async) dependency through
a worker thread pool (Starlette's `run_in_threadpool`, backed by
`anyio.to_thread.run_sync`). Returning a plain value from a sync dependency
crosses that thread boundary fine — but `contextvars.ContextVar.set()` calls
made *inside* that pooled thread do NOT propagate back to the event-loop
context that runs the rest of the request. Each OS thread has its own
independent context stack; there's no automatic bidirectional sync between
a threadpool worker's context mutations and the caller's context. A sync
`get_tenant_context` would call `tenant_scope()`'s `_current.set(ctx)` on a
throwaway worker thread, and every subsequent dependency or the route
handler body — running back on the main event loop — would see the
ContextVar's untouched default (None), hitting core.tenancy's "no tenant
context" guard. Declaring this dependency `async def` keeps it on the event
loop, in the same context as everything downstream, so the tenant_scope()
side effect is actually visible where it's needed.
"""
from __future__ import annotations

from typing import AsyncIterator, Iterator

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core.database import SessionLocal, set_rls_tenant
from core.logging_config import get_logger
from core.security import decode_token
from core.tenancy import TenantContext, tenant_scope

_log = get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    """Per-request DB session. Commits on clean exit, rolls back on exception.
    Route handlers should NOT call db.commit() themselves except for
    fine-grained control in multi-step flows — this dependency handles it.

    Fine to stay a plain sync generator (unlike get_tenant_context below):
    it only returns a Session object across the thread boundary, with no
    ContextVar side effects that need to be visible elsewhere."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    if claims.get("typ") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    return claims


async def get_tenant_context(
    request: Request,
    claims: dict = Depends(get_current_claims),
    db: Session = Depends(get_db),
) -> AsyncIterator[TenantContext]:
    """Establishes the tenant ContextVar for the lifetime of the request via
    an async generator dependency (see module docstring for why it must be
    async), AND arms the Postgres RLS session variable on the same DB
    session route handlers will use. Both layers — ORM filter and RLS —
    are active on every request."""
    tenant_id = claims["tid"]
    user_id = claims["sub"]
    roles = tuple(claims.get("roles", []))

    # Defense-in-depth: also check tenant_id in path/header if present, and
    # reject mismatches (prevents a stale/replayed token from a different
    # tenant subdomain being accepted).
    header_tenant = request.headers.get("X-Tenant-Id")
    if header_tenant and header_tenant != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tenant mismatch")

    set_rls_tenant(db, tenant_id)
    with tenant_scope(tenant_id, user_id, roles) as ctx:
        yield ctx


def require_permission(permission: str):
    """Dependency factory: `Depends(require_permission(Perm.SELLERS_READ))`.
    Checks the permission claim baked into the JWT at login time — no DB
    round-trip needed. Permissions are refreshed on next token refresh, so
    a revoked permission takes effect within the access-token TTL (default
    15 min), not instantly. For instant revocation of a specific user,
    revoke their sessions instead (see services.auth.revoke_session).

    Stays a plain sync function on purpose: it only reads already-resolved
    claims and raises/returns — no ContextVar writes, so the thread-pool
    dispatch that sync dependencies get is harmless here."""

    def _check(claims: dict = Depends(get_current_claims)) -> dict:
        perms = claims.get("perms", [])
        if permission not in perms:
            _log.warning("authz.denied", required=permission, user=claims.get("sub"))
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return claims

    return _check


def get_client_ip(request: Request) -> str | None:
    """Prefer X-Forwarded-For (set by LB/proxy) but only trust it if the
    request comes from a known proxy — otherwise it's spoofable. In this
    reference implementation we trust it; harden with a proxy allowlist
    in front of a real deployment."""
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
