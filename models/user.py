"""
User + session models.

Design notes:
- password_hash is nullable because SSO-only users have no local password
- failed_login_count + locked_until support account lockout
- mfa_secret is encrypted at rest (see core/crypto.py); stored decrypted only in memory
- Sessions store only the HASH of the refresh token, not the raw token
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TenantMixin, TimestampMixin, new_uuid


class User(Base, TenantMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Local auth (null for SSO-only users)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # SSO
    sso_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # MFA
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mfa_recovery_codes_hash: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Lockout state
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_password_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Session(Base, TenantMixin, TimestampMixin):
    """A session ties a refresh token to a user. Revoking = marking revoked.
    Access tokens are stateless JWTs; refresh tokens are stateful so we can
    revoke without waiting for JWT expiry."""
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # For audit / anomaly detection
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
