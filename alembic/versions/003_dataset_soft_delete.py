"""Add deleted_at column to datasets for soft-delete support (Segment 8).

Revision ID: 003_dataset_soft_delete
Revises: 002_dataset_intelligence
Create Date: 2026-07-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_dataset_soft_delete"
down_revision = "002_dataset_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    # Partial index would be ideal (WHERE deleted_at IS NULL) but that's
    # Postgres-specific syntax; a plain index keeps this migration portable
    # across SQLite (used in dev/CI) and Postgres (staging/prod).
    op.create_index("ix_datasets_deleted_at", "datasets", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_datasets_deleted_at", table_name="datasets")
    op.drop_column("datasets", "deleted_at")
