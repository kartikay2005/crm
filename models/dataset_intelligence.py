"""
Dataset Intelligence Engine — persistence for uploads and their profiles.

Segment 1 scope: just the upload record + profile JSON. Domain detection,
model selection, predictions, insights, and reports (Segments 2-7) will add
their own tables/columns here later, referencing `Dataset.id`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TenantMixin, TimestampMixin, new_uuid

# Portable JSON — JSONB on Postgres, plain JSON elsewhere (see models/audit.py
# for the same pattern and why).
PortableJSON = JSON().with_variant(JSONB(), "postgresql")


class Dataset(Base, TenantMixin, TimestampMixin):
    """One uploaded file. `status` tracks pipeline progress so the frontend
    can poll without re-running the whole pipeline synchronously."""
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    uploaded_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    # "uploaded" -> "validated" -> "profiled" -> "domain_detected" ->
    # "model_selected" -> "predicted" -> "complete" | "failed"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DatasetProfileRecord(Base, TenantMixin, TimestampMixin):
    """Stored output of services.dataset_intelligence.profiler.profile_dataset,
    serialized as JSON so later pipeline stages (domain detection, schema
    matching) can read it back without re-parsing the source file."""
    __tablename__ = "dataset_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, unique=True)
    profile_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False)


class DatasetAnalysisRecord(Base, TenantMixin, TimestampMixin):
    """Stored output of Segment 2: domain detection + quality/readiness
    scores. Kept separate from DatasetProfileRecord since profiling is
    format/structure analysis while this is interpretation — different
    pipeline stages, different failure modes, and Segment 3+ will extend
    this table (or add siblings) for model selection and prediction
    results without touching the profile record."""
    __tablename__ = "dataset_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, unique=True)

    top_domain: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_confidence: Mapped[float] = mapped_column(nullable=False)
    domain_band: Mapped[str] = mapped_column(String(16), nullable=False)
    domain_scores_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False)

    quality_score: Mapped[float] = mapped_column(nullable=False)
    quality_label: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False)

    readiness_score: Mapped[float] = mapped_column(nullable=False)
    readiness_label: Mapped[str] = mapped_column(String(16), nullable=False)
    readiness_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False)

    # Segment 3: model selection outcome. selected_model_name is NULL when
    # the fallback path was taken — that's the expected, healthy state for
    # a low-confidence or unmatched dataset, not an error condition.
    selected_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected_model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schema_match_pct: Mapped[float | None] = mapped_column(nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    considered_models_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=list)


class DatasetPredictionRecord(Base, TenantMixin, TimestampMixin):
    """Stored output of Segment 4: real inference + SHAP explanations from
    the model DatasetAnalysisRecord selected. Absent entirely when the
    fallback path was taken (no model selected) — that's the expected,
    healthy state, not a missing-data bug; callers should check for the
    row's existence rather than assume one always exists per dataset."""
    __tablename__ = "dataset_predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, unique=True)

    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    problem_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_variable: Mapped[str] = mapped_column(String(255), nullable=False)

    n_rows_predicted: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_dropped_missing_features: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inference_time_seconds: Mapped[float] = mapped_column(nullable=False)
    rows_per_second: Mapped[float] = mapped_column(nullable=False)

    prediction_distribution_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    row_predictions_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=list)

    explainer_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    global_importance_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    local_explanations_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=list)

    explainability_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DatasetInsightRecord(Base, TenantMixin, TimestampMixin):
    """Stored output of Segment 5: insights + recommendations. Unlike
    DatasetPredictionRecord, this ALWAYS gets created — insights run even
    on the fallback (no-model-selected) path, since exploratory insights
    are explicitly what the spec promises to show in that case."""
    __tablename__ = "dataset_insights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, unique=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    insights_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    recommendations_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=list)
