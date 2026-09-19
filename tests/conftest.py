"""
Shared pytest fixtures for top-level tests (outside tests/dataset_intelligence/,
which has its own conftest.py). Uses SQLite for speed, same pattern as the
dataset_intelligence suite.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENV", "local")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-not-for-prod-32-chars-min")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "")
os.environ.setdefault("REDIS_URL", "")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.tenancy import install_tenant_query_filter, tenant_scope
from models.base import Base, install_insert_guard, new_uuid
from models.rbac import DEFAULT_ROLE_PERMISSIONS, Role, RolePermission, UserRole
from models.tenant import Tenant
from models.user import User
from services import auth as auth_service

# Import every model module so Base.metadata is fully populated — same
# requirement as alembic/env.py and tests/dataset_intelligence/conftest.py.
from models import audit, config as config_models, dataset_intelligence, feedback  # noqa: F401


@pytest.fixture()
def db_engine():
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
    (tenant_id, user_id, email, password)."""
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
