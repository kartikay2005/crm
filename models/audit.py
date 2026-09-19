"""
Immutable audit log with tamper-evidence.

Every entry includes the SHA-256 hash of the previous entry (per-tenant chain).
Any modification to a past row breaks the chain and is detectable by a
verification job. Combine with append-only DB permissions (GRANT INSERT only
to the app user, no UPDATE/DELETE) for defense-in-depth.

For regulated environments (SOX, HIPAA, PCI), export this table to WORM storage
(S3 Object Lock, Azure immutable blob) daily.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Index, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TenantMixin, new_uuid

# Portable JSON type: renders as JSONB (indexable, faster) on Postgres,
# falls back to generic JSON (stored as TEXT) on SQLite and others. This
# lets local dev / CI run on SQLite without a Postgres dependency while
# production gets JSONB's query and indexing benefits.
PortableJSON = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(Base, TenantMixin):
    """No updated_at, no timestamp mixin — this table is append-only.

    `seq` (not `id`) is the true ordering key for the hash chain. `id` is a
    UUID used as the stable external identifier, but UUIDs are randomly
    generated and therefore useless as a tie-breaker when two events share
    the same `occurred_at` timestamp (very possible under fast successive
    writes — plain wall-clock resolution isn't fine enough to guarantee
    uniqueness). `seq` is a real autoincrementing integer, so ordering by it
    always reflects true insertion order, which both the chain-building
    query (audit_event's "find the previous row") and verify_chain rely on
    agreeing about.
    """
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_audit_tenant_actor", "tenant_id", "actor_user_id"),
        Index("ix_audit_tenant_event", "tenant_id", "event_type"),
        Index("ix_audit_tenant_seq", "tenant_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), default=new_uuid, unique=True, index=True, nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "user.login", "user.login_failed", "seller.view", "recommendation.dismissed",
    #      "email.sent", "config.updated", "user.role_granted"

    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Full event payload. Use JSONB on Postgres for query-ability.
    payload: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)

    # Tamper-evidence: sha256(prev_hash || row_canonical_json)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Correlation for tracing a request through multiple audit events
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
