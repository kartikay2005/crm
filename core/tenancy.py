"""
Multi-tenancy enforcement.

Approach: **row-level isolation** via a mandatory `tenant_id` column on every
tenant-scoped table, enforced at the ORM layer via a session-level filter,
plus optional Postgres RLS policies (see alembic/versions/001_initial.py) for
defense-in-depth.

The TenantContext is a ContextVar so it's safe under async and threaded workers.
Any query that touches a tenant-scoped model without a tenant context in place
raises immediately — this is a fail-loud design so misconfiguration can't
silently leak data across tenants.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import String, event
from sqlalchemy.orm import Mapped, Session, mapped_column, with_loader_criteria

# ---- Context ----------------------------------------------------------------


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str
    roles: tuple[str, ...]


_current: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)


def current_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError(
            "No tenant context set. Every request must establish one before "
            "touching tenant-scoped data. This is a bug in the calling code, "
            "not a data issue."
        )
    return ctx


def current_tenant_or_none() -> TenantContext | None:
    """For code paths that are legitimately cross-tenant (e.g. platform admin
    operations, background reconciliation jobs). Use sparingly and audit."""
    return _current.get()


_bypass_reason: ContextVar[str | None] = ContextVar("tenant_bypass_reason", default=None)


@contextmanager
def tenant_scope(tenant_id: str, user_id: str, roles: tuple[str, ...]) -> Iterator[TenantContext]:
    """Establish a tenant context for the duration of a request or job.

    Restores the previous value via `_current.set(previous)` rather than
    `token.reset()` on purpose: FastAPI executes sync generator dependencies
    (like api.deps.get_tenant_context, which wraps this) via a worker thread
    pool, and separate calls to a generator's __enter__/__exit__ are not
    guaranteed to land on the same OS thread. `ContextVar.Token.reset()`
    validates that it's being reset in the same Context it was created in
    and raises ValueError otherwise — which reliably crashes real requests
    under this dependency pattern. `ContextVar.set()` has no such
    restriction, so capturing and restoring the previous value directly
    sidesteps the problem regardless of which thread runs which half.

    CAUTION for batch/background jobs that loop over multiple tenants: this
    context manager only sets the ContextVar — it does NOT give you a fresh
    DB Session. If you reuse one Session across multiple tenant_scope()
    blocks for different tenants, SQLAlchemy's identity map can return
    already-loaded rows from a prior tenant with no query and therefore no
    filtering at all (see core.database.session_scope_for_tenant for the
    full explanation and the required pattern). The normal API request path
    is unaffected — api.deps.get_db already opens one Session per request,
    before any tenant is known, so no Session there ever spans two tenants.
    """
    ctx = TenantContext(tenant_id=tenant_id, user_id=user_id, roles=roles)
    previous = _current.get()
    _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.set(previous)


@contextmanager
def unscoped_bootstrap_query(reason: str) -> Iterator[None]:
    """Narrow, explicit escape hatch for the handful of legitimate lookups
    that must run BEFORE a tenant is known — e.g. finding a session row by
    its unique refresh-token hash, where the token itself (not tenant
    scoping) is the security boundary, or resolving a tenant from a login
    slug. Every use is logged with its reason so it's auditable and
    grep-able (`unscoped_bootstrap_query(`) in code review.

    This does NOT bypass the insert guard — you still cannot write a row
    without a real tenant context. It only relaxes the SELECT filter, and
    only for the duration of the `with` block. Once the bootstrap query
    finds the tenant_id it needs, callers should immediately switch to a
    real `tenant_scope(...)` for everything else.
    """
    from core.logging_config import get_logger
    _log = get_logger("core.tenancy.bootstrap")
    _log.info("tenancy.unscoped_bootstrap_query", reason=reason)

    previous = _bypass_reason.get()
    _bypass_reason.set(reason)
    try:
        yield
    finally:
        _bypass_reason.set(previous)


# ---- ORM enforcement --------------------------------------------------------


class TenantScopedMixin:
    """Declarative mixin: marks a model as tenant-scoped AND declares the
    actual `tenant_id` mapped column. Both live on the same class
    deliberately — with_loader_criteria() needs the root class it's given
    to have a real mapped attribute for its lambda-analysis/caching step to
    resolve `cls.tenant_id` against, not just a marker flag. Any subclass
    that also inherits Base gets a real `tenant_id` column via normal
    SQLAlchemy declarative-mixin column inheritance."""

    __tenant_scoped__ = True
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)


def install_tenant_query_filter(session: Session) -> None:
    """Register a before_execute hook that injects tenant_id filters on any
    SELECT touching a TenantScopedMixin subclass. Call this once during app
    startup on the sessionmaker.

    This is layered on top of Postgres RLS — if either layer is bypassed, the
    other still prevents cross-tenant leakage.
    """

    @event.listens_for(session, "do_orm_execute")
    def _tenant_filter(execute_state):
        if not execute_state.is_select or execute_state.is_relationship_load:
            return

        ctx = current_tenant_or_none()
        if ctx is None:
            if _bypass_reason.get() is not None:
                # Explicit, logged bootstrap bypass — run unfiltered. Caller
                # is responsible for re-scoping to a real tenant immediately
                # after this query using the tenant_id found in the result.
                return
            # No context, no bypass — refuse to run the query if it would
            # hit a tenant-scoped model. Fail loud: this is a bug in the
            # calling code, not a data issue.
            for mapper in execute_state.all_mappers:
                if getattr(mapper.class_, "__tenant_scoped__", False):
                    raise RuntimeError(
                        f"Query on {mapper.class_.__name__} attempted with no tenant "
                        f"context and no unscoped_bootstrap_query() justification"
                    )
            return

        # Inject WHERE tenant_id = :tenant_id on every tenant-scoped entity.
        # NOTE: capture a plain str local (not ctx.tenant_id via attribute
        # access inside the lambda) — SQLAlchemy's lambda-caching layer
        # requires closure variables to be literal/cacheable bind values,
        # and an attribute access on a dataclass instance doesn't qualify.
        tenant_id_for_filter = ctx.tenant_id
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                TenantScopedMixin,
                lambda cls: cls.tenant_id == tenant_id_for_filter,
                include_aliases=True,
            )
        )


def enforce_tenant_on_insert(instance, tenant_scoped_classes: set[type]) -> None:
    """Called from a before_flush hook — see models/base.py. Ensures every
    inserted tenant-scoped row has tenant_id set (and set to the CURRENT tenant,
    not some other one from a stale reference)."""
    if type(instance) not in tenant_scoped_classes:
        return
    ctx = current_tenant()
    current_id = getattr(instance, "tenant_id", None)
    if current_id is None:
        instance.tenant_id = ctx.tenant_id
    elif current_id != ctx.tenant_id:
        raise PermissionError(
            f"Attempted to insert {type(instance).__name__} with tenant_id "
            f"{current_id!r} while current context is {ctx.tenant_id!r}"
        )
