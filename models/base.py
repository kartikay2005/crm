"""
Declarative base + tenant-enforcement hooks.

Every model that stores tenant data inherits from both `Base` and
`TenantMixin`, which gets it a mandatory, indexed `tenant_id` column.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, event, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.tenancy import TenantScopedMixin, enforce_tenant_on_insert


class Base(DeclarativeBase):
    pass


def new_uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# TenantMixin is the model-layer name for core.tenancy's TenantScopedMixin,
# which declares both the __tenant_scoped__ marker and the actual mapped
# tenant_id column on one class (see core/tenancy.py for why they must be
# on the same class — with_loader_criteria needs a real attribute to
# resolve, not just a marker). Kept as a separate name here purely so model
# files read naturally (`class User(Base, TenantMixin, ...)`) without every
# model needing to import from core.tenancy directly.
TenantMixin = TenantScopedMixin


# Collect tenant-scoped classes for the insert-time enforcement hook
_TENANT_SCOPED: set[type] = set()


@event.listens_for(Base, "instrument_class", propagate=True)
def _register_tenant_class(mapper, cls):
    if getattr(cls, "__tenant_scoped__", False):
        _TENANT_SCOPED.add(cls)


def install_insert_guard(SessionLocal) -> None:
    """Register a before_flush hook on the sessionmaker that verifies tenant
    integrity on every insert. Call once at app startup."""

    @event.listens_for(SessionLocal, "before_flush")
    def _guard(session, flush_context, instances):
        for obj in session.new:
            enforce_tenant_on_insert(obj, _TENANT_SCOPED)
