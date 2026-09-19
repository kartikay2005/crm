"""
Shared pytest fixtures for the dataset intelligence test suite.

Uses SQLite for speed — appropriate for logic/behavior tests. Postgres-
specific behavior (Row-Level Security) is NOT exercisable on SQLite and is
covered separately by scripts/test_postgres_rls.py (see that file's
docstring for why it's a standalone script rather than a pytest fixture:
it needs a real Postgres server, which pytest's default CI run doesn't
provision).
"""
from __future__ import annotations

import os

os.environ.setdefault("ENV", "local")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-not-for-prod-32-chars-min")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "")
os.environ.setdefault("REDIS_URL", "")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.tenancy import install_tenant_query_filter, tenant_scope
from models.base import Base, install_insert_guard, new_uuid
from models.rbac import DEFAULT_ROLE_PERMISSIONS, Role, RolePermission, UserRole
from models.tenant import Tenant
from models.user import User
from services import auth as auth_service

# Import every model module so Base.metadata is fully populated — same
# requirement as alembic/env.py, and the same bug class (silently missing
# tables) bit this project once already during Segment 5's migration work.
from models import audit, config as config_models, dataset_intelligence, feedback  # noqa: F401


@pytest.fixture()
def db_engine():
    # StaticPool + check_same_thread=False: the standard pattern for
    # testing with in-memory SQLite in a multi-threaded app. Without
    # StaticPool, SQLAlchemy's default SingletonThreadPool gives each
    # thread its own separate, empty in-memory database — and FastAPI
    # dispatches sync dependencies (like api.deps.get_db) via a worker
    # thread pool, so the request thread would see a table-less DB
    # distinct from the one this fixture just populated. Same underlying
    # class of issue as the ContextVar-across-threadpool bug documented in
    # api/deps.py's get_tenant_context, just showing up in SQLite's pool
    # behavior instead of contextvars this time.
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def SessionLocal(db_engine):
    factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    install_tenant_query_filter(factory)
    install_insert_guard(factory)
    return factory


@pytest.fixture()
def db(SessionLocal):
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def tenant_and_user(db):
    """Creates a tenant with one account_manager user. Returns
    (tenant_id, user_id, email, password) for use in auth flows."""
    email, password = "jordan@acme.com", "CorrectHorseBattery9!"
    tenant = Tenant(id=new_uuid(), slug="acme", display_name="Acme")
    db.add(tenant)
    db.flush()
    with tenant_scope(tenant.id, "system", ("system",)):
        role = Role(id=new_uuid(), tenant_id=tenant.id, name="account_manager")
        db.add(role)
        db.flush()
        for perm in DEFAULT_ROLE_PERMISSIONS["account_manager"]:
            db.add(RolePermission(id=new_uuid(), tenant_id=tenant.id, role_id=role.id, permission=perm))
        db.flush()
        user = User(
            id=new_uuid(), tenant_id=tenant.id, email=email, display_name="Jordan",
            password_hash=auth_service.security.hash_password(password),
        )
        db.add(user)
        db.flush()
        db.add(UserRole(id=new_uuid(), tenant_id=tenant.id, user_id=user.id, role_id=role.id))
        db.commit()
    return tenant.id, user.id, email, password


@pytest.fixture()
def second_tenant_and_user(db):
    """A second, independent tenant — for cross-tenant isolation tests."""
    email, password = "sam@globex.com", "CorrectHorseBattery9!"
    tenant = Tenant(id=new_uuid(), slug="globex", display_name="Globex")
    db.add(tenant)
    db.flush()
    with tenant_scope(tenant.id, "system", ("system",)):
        role = Role(id=new_uuid(), tenant_id=tenant.id, name="account_manager")
        db.add(role)
        db.flush()
        for perm in DEFAULT_ROLE_PERMISSIONS["account_manager"]:
            db.add(RolePermission(id=new_uuid(), tenant_id=tenant.id, role_id=role.id, permission=perm))
        db.flush()
        user = User(
            id=new_uuid(), tenant_id=tenant.id, email=email, display_name="Sam",
            password_hash=auth_service.security.hash_password(password),
        )
        db.add(user)
        db.flush()
        db.add(UserRole(id=new_uuid(), tenant_id=tenant.id, user_id=user.id, role_id=role.id))
        db.commit()
    return tenant.id, user.id, email, password


@pytest.fixture()
def api_client(SessionLocal, monkeypatch):
    """A TestClient wired to the same SQLite engine as the `db` fixture —
    critical detail: api.deps.get_db creates its OWN SessionLocal at import
    time (core.database.SessionLocal), so this fixture monkeypatches that
    reference to point at the test engine's sessionmaker instead. Without
    this, the API layer would silently talk to a different (empty)
    database than the one test fixtures seed data into."""
    import api.deps as deps_module
    import core.database as database_module

    monkeypatch.setattr(database_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(deps_module, "SessionLocal", SessionLocal)

    from api.main import app
    return TestClient(app)


@pytest.fixture()
def auth_headers(api_client, tenant_and_user):
    _, _, email, password = tenant_and_user
    resp = api_client.post("/api/v1/auth/login", json={
        "tenant_slug": "acme", "email": email, "password": password,
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def second_auth_headers(api_client, second_tenant_and_user):
    _, _, email, password = second_tenant_and_user
    resp = api_client.post("/api/v1/auth/login", json={
        "tenant_slug": "globex", "email": email, "password": password,
    })
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
