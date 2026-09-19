"""
Role-Based Access Control.

Permissions are strings like "sellers:read", "sellers:write", "admin:tenant".
Roles bundle permissions. Users have roles (per-tenant).

Adding a permission = adding a string constant + updating role assignments.
No code changes to models needed.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TenantMixin, TimestampMixin, new_uuid


# Canonical permission strings — reference these from services rather than
# hard-coding strings, so a rename is a single-file change.
class Perm:
    SELLERS_READ = "sellers:read"
    SELLERS_WRITE = "sellers:write"
    RECOMMENDATIONS_READ = "recommendations:read"
    RECOMMENDATIONS_FEEDBACK = "recommendations:feedback"
    EMAILS_GENERATE = "emails:generate"
    EMAILS_SEND = "emails:send"
    REPORTS_READ = "reports:read"
    REPORTS_EXPORT = "reports:export"
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"
    USERS_MANAGE = "users:manage"
    AUDIT_READ = "audit:read"
    TENANT_ADMIN = "tenant:admin"
    PLATFORM_ADMIN = "platform:admin"  # cross-tenant, sparingly granted
    DATASETS_READ = "datasets:read"
    DATASETS_WRITE = "datasets:write"


DEFAULT_ROLE_PERMISSIONS = {
    "account_manager": [
        Perm.SELLERS_READ,
        Perm.RECOMMENDATIONS_READ,
        Perm.RECOMMENDATIONS_FEEDBACK,
        Perm.EMAILS_GENERATE,
        Perm.EMAILS_SEND,
        Perm.REPORTS_READ,
        Perm.DATASETS_READ,
        Perm.DATASETS_WRITE,
    ],
    "team_lead": [
        Perm.SELLERS_READ,
        Perm.SELLERS_WRITE,
        Perm.RECOMMENDATIONS_READ,
        Perm.RECOMMENDATIONS_FEEDBACK,
        Perm.EMAILS_GENERATE,
        Perm.EMAILS_SEND,
        Perm.REPORTS_READ,
        Perm.REPORTS_EXPORT,
        Perm.CONFIG_READ,
        Perm.AUDIT_READ,
        Perm.DATASETS_READ,
        Perm.DATASETS_WRITE,
    ],
    "executive_viewer": [
        Perm.SELLERS_READ,
        Perm.RECOMMENDATIONS_READ,
        Perm.REPORTS_READ,
        Perm.REPORTS_EXPORT,
        Perm.DATASETS_READ,
    ],
    "tenant_admin": [
        Perm.SELLERS_READ,
        Perm.SELLERS_WRITE,
        Perm.RECOMMENDATIONS_READ,
        Perm.RECOMMENDATIONS_FEEDBACK,
        Perm.EMAILS_GENERATE,
        Perm.EMAILS_SEND,
        Perm.REPORTS_READ,
        Perm.REPORTS_EXPORT,
        Perm.CONFIG_READ,
        Perm.CONFIG_WRITE,
        Perm.USERS_MANAGE,
        Perm.AUDIT_READ,
        Perm.TENANT_ADMIN,
        Perm.DATASETS_READ,
        Perm.DATASETS_WRITE,
    ],
}


class Role(Base, TenantMixin, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    permissions: Mapped[list["RolePermission"]] = relationship(cascade="all, delete-orphan")


class RolePermission(Base, TenantMixin, TimestampMixin):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uq_role_permission"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.id"), nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)


class UserRole(Base, TenantMixin, TimestampMixin):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.id"), nullable=False, index=True)
