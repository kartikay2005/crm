"""
Database engine + session management.

- Connection pooling tuned for a web workload (see config for defaults)
- Pre-ping to detect stale connections after network blips
- Explicit health check for readiness probes
- Session lifecycle managed by dependency-injected context — never module-global
"""
from __future__ import annotations

import contextlib
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings

_settings = get_settings()


def _build_engine() -> Engine:
    url = _settings.database_url.get_secret_value()
    connect_args: dict = {}
    # For Postgres, set a server-side statement timeout so runaway queries
    # can't tie up a pool connection indefinitely.
    if url.startswith("postgresql"):
        connect_args["options"] = "-c statement_timeout=30000"  # 30s

    # pool_size / max_overflow / pool_timeout are QueuePool-specific
    # arguments. SQLAlchemy auto-selects QueuePool for file-based SQLite
    # (sqlite:///path.db) but SingletonThreadPool for in-memory SQLite
    # (sqlite:///:memory:) — the latter does NOT accept these kwargs and
    # raises a hard TypeError on engine construction if given them. This
    # went unnoticed through 8 prior segments of testing because every ad
    # hoc test script used file-based SQLite; the first in-memory pytest
    # suite (built for speed, see tests/dataset_intelligence/conftest.py)
    # caught it immediately. Only pass these for Postgres, where they're
    # both valid and actually meaningful.
    pool_kwargs: dict = {}
    if url.startswith("postgresql"):
        pool_kwargs = {
            "pool_size": _settings.database_pool_size,
            "max_overflow": _settings.database_max_overflow,
            "pool_timeout": _settings.database_pool_timeout_seconds,
        }

    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,  # recycle connections after 30 min (avoids stale conns)
        echo=_settings.database_echo,
        connect_args=connect_args,
        future=True,
        **pool_kwargs,
    )

    # Enforce SET application_name so DBAs can identify workload in pg_stat_activity
    if url.startswith("postgresql"):
        @event.listens_for(engine, "connect")
        def _set_app_name(dbapi_conn, _):
            with dbapi_conn.cursor() as cur:
                cur.execute(f"SET application_name = '{_settings.app_name}-{_settings.env}'")

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context. Commits on success, rolls back on error.
    Always use this over raw SessionLocal() so leaks and dangling transactions
    can't happen."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_rls_tenant(session: Session, tenant_id: str) -> None:
    """Set the Postgres session variable that RLS policies (see
    alembic/versions/001_initial_schema.py) check on every row. Call this at
    the start of every request after the tenant context is established —
    core.tenancy.tenant_scope's ORM-level filter and this DB-level policy
    are independent layers; either one alone stops cross-tenant leakage.

    `is_local=true` scopes the setting to the current transaction, so it
    can never leak across pooled connections between requests.
    """
    if not engine.url.get_backend_name().startswith("postgresql"):
        return  # RLS policies only exist on Postgres; no-op elsewhere (e.g. tests on SQLite)
    session.execute(text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                     {"tid": tenant_id})


def check_database_health() -> bool:
    """Cheap readiness probe. Returns True if the DB responds to SELECT 1."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@contextlib.contextmanager
def session_scope_for_tenant(tenant_id: str, user_id: str, roles: tuple[str, ...]) -> Iterator[Session]:
    """The REQUIRED pattern for any code that loops over multiple tenants in
    a single process (nightly batch jobs, feedback aggregation, model
    retraining, etc).

    *** Why this exists — a real footgun, not theoretical ***
    SQLAlchemy's identity map is a per-Session, primary-key-keyed cache.
    `session.get(Model, pk)` checks the identity map BEFORE issuing any SQL —
    if the row was already loaded earlier in the same Session (even under a
    *different* tenant_scope()), it's returned straight from memory with NO
    query, and therefore no chance for the tenant filter (or Postgres RLS,
    which only applies to actual SQL) to intervene. `expire_all()` does NOT
    fix this — it marks columns stale for next access, it does not remove
    objects from the identity map. `expunge_all()` does remove them, but
    remembering to call it correctly at every tenant boundary is exactly the
    kind of manual discipline that gets forgotten under deadline pressure.

    This is invisible in the normal API request path because `api.deps.get_db`
    opens a brand-new Session per HTTP request, so no Session ever sees two
    tenants. But a batch job written as:
        for tenant in all_tenants:
            with tenant_scope(tenant.id, ...):
                do_stuff(shared_session)
    is vulnerable. Use this function instead — it pairs a fresh Session
    (fresh, empty identity map) with the tenant context in one call, so the
    unsafe pattern above isn't reachable by accident:

        for tenant in all_tenants:
            with session_scope_for_tenant(tenant.id, "system", ("system",)) as db:
                do_stuff(db)
    """
    from core.tenancy import tenant_scope

    with tenant_scope(tenant_id, user_id, roles):
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
