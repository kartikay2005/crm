"""Initial schema: tenants, users, sessions, RBAC, audit log, feedback loop,
category thresholds.

Also enables Postgres Row-Level Security on every tenant-scoped table as a
second, DB-enforced layer of tenant isolation — independent of the ORM-level
filter in core/tenancy.py. Even a bug or a raw psql session under the app's
DB role cannot cross tenants once RLS is enabled and FORCED.

The app's DB role must SET the session variable `app.current_tenant_id`
before querying (see core/database.py — add this in the connection
checkout hook for the app's runtime role). Migration/admin roles should
BYPASSRLS.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-07-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Matches models/audit.py PortableJSON — JSONB on Postgres, JSON elsewhere.
_PORTABLE_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


TENANT_SCOPED_TABLES = [
    "users", "sessions", "roles", "role_permissions", "user_roles",
    "audit_log", "recommendations", "recommendation_feedback",
    "category_thresholds", "learned_weights",
]


def upgrade() -> None:
    # -------------------------------------------------------------------
    # tenants (not itself tenant-scoped)
    # -------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("data_region", sa.String(32), nullable=False, server_default="us-east-1"),
        sa.Column("sso_config", sa.String(4000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # -------------------------------------------------------------------
    # users
    # -------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("sso_subject", sa.String(255), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("mfa_secret_encrypted", sa.String(500), nullable=True),
        sa.Column("mfa_recovery_codes_hash", sa.String(2000), nullable=True),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_password_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_sso_subject", "users", ["sso_subject"])

    # -------------------------------------------------------------------
    # sessions
    # -------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sessions_tenant_id", "sessions", ["tenant_id"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_refresh_token_hash", "sessions", ["refresh_token_hash"], unique=True)

    # -------------------------------------------------------------------
    # roles / role_permissions / user_roles
    # -------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("role_id", sa.String(36), nullable=False),
        sa.Column("permission", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("role_id", "permission", name="uq_role_permission"),
    )
    op.create_index("ix_role_permissions_tenant_id", "role_permissions", ["tenant_id"])
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"])

    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )
    op.create_index("ix_user_roles_tenant_id", "user_roles", ["tenant_id"])
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"])
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    # -------------------------------------------------------------------
    # audit_log (append-only — no updated_at)
    # -------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("actor_ip", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("payload", _PORTABLE_JSON, nullable=True),
        sa.Column("prev_hash", sa.String(64), nullable=True),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint("row_hash"),
    )
    op.create_index("ix_audit_log_id", "audit_log", ["id"])
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_occurred_at", "audit_log", ["occurred_at"])
    op.create_index("ix_audit_log_request_id", "audit_log", ["request_id"])
    op.create_index("ix_audit_tenant_time", "audit_log", ["tenant_id", "occurred_at"])
    op.create_index("ix_audit_tenant_actor", "audit_log", ["tenant_id", "actor_user_id"])
    op.create_index("ix_audit_tenant_event", "audit_log", ["tenant_id", "event_type"])
    op.create_index("ix_audit_tenant_seq", "audit_log", ["tenant_id", "seq"])

    # -------------------------------------------------------------------
    # recommendations / recommendation_feedback
    # -------------------------------------------------------------------
    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("seller_id", sa.String(64), nullable=False),
        sa.Column("shown_to_user_id", sa.String(36), nullable=False),
        sa.Column("recommendation_type", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("features_snapshot", sa.Text, nullable=True),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_recommendations_tenant_id", "recommendations", ["tenant_id"])
    op.create_index("ix_reco_tenant_seller", "recommendations", ["tenant_id", "seller_id"])
    op.create_index("ix_reco_tenant_user_time", "recommendations",
                     ["tenant_id", "shown_to_user_id", "created_at"])

    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("recommendation_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("action_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_recommendation_feedback_tenant_id", "recommendation_feedback", ["tenant_id"])
    op.create_index("ix_feedback_reco", "recommendation_feedback", ["recommendation_id"])
    op.create_index("ix_feedback_tenant_time", "recommendation_feedback", ["tenant_id", "created_at"])

    # -------------------------------------------------------------------
    # category_thresholds / learned_weights
    # -------------------------------------------------------------------
    op.create_table(
        "category_thresholds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column("weight_revenue", sa.Float, nullable=False, server_default="0.20"),
        sa.Column("weight_growth", sa.Float, nullable=False, server_default="0.20"),
        sa.Column("weight_returns", sa.Float, nullable=False, server_default="0.15"),
        sa.Column("weight_shipping", sa.Float, nullable=False, server_default="0.15"),
        sa.Column("weight_rating", sa.Float, nullable=False, server_default="0.10"),
        sa.Column("weight_conversion", sa.Float, nullable=False, server_default="0.10"),
        sa.Column("weight_support", sa.Float, nullable=False, server_default="0.10"),
        sa.Column("high_return_rate", sa.Float, nullable=False, server_default="0.12"),
        sa.Column("declining_growth_rate", sa.Float, nullable=False, server_default="-0.03"),
        sa.Column("late_shipment_threshold", sa.Float, nullable=False, server_default="0.08"),
        sa.Column("low_rating_threshold", sa.Float, nullable=False, server_default="4.0"),
        sa.Column("contact_cadence_healthy_days", sa.Integer, nullable=False, server_default="30"),
        sa.Column("contact_cadence_at_risk_days", sa.Integer, nullable=False, server_default="7"),
        sa.Column("contact_cadence_critical_days", sa.Integer, nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "category", "region", name="uq_thresholds_tenant_cat_region"),
    )
    op.create_index("ix_category_thresholds_tenant_id", "category_thresholds", ["tenant_id"])
    op.create_index("ix_thresholds_lookup", "category_thresholds", ["tenant_id", "category", "region"])

    op.create_table(
        "learned_weights",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("weight_multiplier", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("positive_feedback_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("negative_feedback_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "action", "category", name="uq_learned_tenant_action_cat"),
    )
    op.create_index("ix_learned_weights_tenant_id", "learned_weights", ["tenant_id"])

    # -------------------------------------------------------------------
    # Row-Level Security — second, DB-enforced layer of tenant isolation.
    # No-ops on non-Postgres backends (guarded by bind check).
    # -------------------------------------------------------------------
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in TENANT_SCOPED_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
            """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in TENANT_SCOPED_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")

    op.drop_table("learned_weights")
    op.drop_table("category_thresholds")
    op.drop_table("recommendation_feedback")
    op.drop_table("recommendations")
    op.drop_table("audit_log")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("tenants")
