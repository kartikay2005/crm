"""Dataset Intelligence Engine tables: datasets, dataset_profiles,
dataset_analyses, dataset_predictions, dataset_insights.

These were built across Segments 1-5 but never previously migrated — all
testing during those segments used Base.metadata.create_all() directly
against SQLite as a dev shortcut. This migration brings them in line with
the rest of the schema (real migration, RLS policies, proper indexes) in
one pass since none of the five has ever shipped to a real database yet.

Revision ID: 002_dataset_intelligence
Revises: 001_initial_schema
Create Date: 2026-07-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "002_dataset_intelligence"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None

# Matches models/dataset_intelligence.py's PortableJSON — JSONB on
# Postgres, plain JSON (stored as TEXT) elsewhere.
_PORTABLE_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

TENANT_SCOPED_TABLES = [
    "datasets", "dataset_profiles", "dataset_analyses",
    "dataset_predictions", "dataset_insights",
]


def upgrade() -> None:
    # -------------------------------------------------------------------
    # datasets
    # -------------------------------------------------------------------
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(36), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("extension", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_datasets_tenant_id", "datasets", ["tenant_id"])

    # -------------------------------------------------------------------
    # dataset_profiles
    # -------------------------------------------------------------------
    op.create_table(
        "dataset_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("profile_json", _PORTABLE_JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id"),
    )
    op.create_index("ix_dataset_profiles_tenant_id", "dataset_profiles", ["tenant_id"])

    # -------------------------------------------------------------------
    # dataset_analyses
    # -------------------------------------------------------------------
    op.create_table(
        "dataset_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("top_domain", sa.String(64), nullable=False),
        sa.Column("domain_confidence", sa.Float, nullable=False),
        sa.Column("domain_band", sa.String(16), nullable=False),
        sa.Column("domain_scores_json", _PORTABLE_JSON, nullable=False),
        sa.Column("quality_score", sa.Float, nullable=False),
        sa.Column("quality_label", sa.String(16), nullable=False),
        sa.Column("quality_json", _PORTABLE_JSON, nullable=False),
        sa.Column("readiness_score", sa.Float, nullable=False),
        sa.Column("readiness_label", sa.String(16), nullable=False),
        sa.Column("readiness_json", _PORTABLE_JSON, nullable=False),
        sa.Column("selected_model_name", sa.String(255), nullable=True),
        sa.Column("selected_model_version", sa.String(32), nullable=True),
        sa.Column("schema_match_pct", sa.Float, nullable=True),
        sa.Column("fallback_reason", sa.Text, nullable=True),
        sa.Column("considered_models_json", _PORTABLE_JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id"),
    )
    op.create_index("ix_dataset_analyses_tenant_id", "dataset_analyses", ["tenant_id"])

    # -------------------------------------------------------------------
    # dataset_predictions
    # -------------------------------------------------------------------
    op.create_table(
        "dataset_predictions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("problem_type", sa.String(32), nullable=False),
        sa.Column("target_variable", sa.String(255), nullable=False),
        sa.Column("n_rows_predicted", sa.Integer, nullable=False),
        sa.Column("rows_dropped_missing_features", sa.Integer, nullable=False, server_default="0"),
        sa.Column("inference_time_seconds", sa.Float, nullable=False),
        sa.Column("rows_per_second", sa.Float, nullable=False),
        sa.Column("prediction_distribution_json", _PORTABLE_JSON, nullable=False),
        sa.Column("row_predictions_json", _PORTABLE_JSON, nullable=False, server_default="[]"),
        sa.Column("explainer_type", sa.String(64), nullable=True),
        sa.Column("global_importance_json", _PORTABLE_JSON, nullable=False, server_default="{}"),
        sa.Column("local_explanations_json", _PORTABLE_JSON, nullable=False, server_default="[]"),
        sa.Column("explainability_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id"),
    )
    op.create_index("ix_dataset_predictions_tenant_id", "dataset_predictions", ["tenant_id"])

    # -------------------------------------------------------------------
    # dataset_insights
    # -------------------------------------------------------------------
    op.create_table(
        "dataset_insights",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("insights_json", _PORTABLE_JSON, nullable=False, server_default="{}"),
        sa.Column("recommendations_json", _PORTABLE_JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id"),
    )
    op.create_index("ix_dataset_insights_tenant_id", "dataset_insights", ["tenant_id"])

    # -------------------------------------------------------------------
    # Row-Level Security — same pattern as 001_initial_schema.py. No-op on
    # non-Postgres backends.
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

    op.drop_table("dataset_insights")
    op.drop_table("dataset_predictions")
    op.drop_table("dataset_analyses")
    op.drop_table("dataset_profiles")
    op.drop_table("datasets")
