"""
Tenant = an isolated customer of the CRM platform. Every seller, user,
audit event, and configuration row is scoped to exactly one tenant.
"""
from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin, new_uuid


class Tenant(Base, TimestampMixin):
    """A tenant is NOT itself tenant-scoped — it IS the tenant. Only platform
    admins can list/create tenants."""
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Compliance / residency
    data_region: Mapped[str] = mapped_column(String(32), default="us-east-1", nullable=False)

    # SSO / auth config (JSON-serialized OIDC config per tenant)
    sso_config: Mapped[str | None] = mapped_column(String(4000), nullable=True)
