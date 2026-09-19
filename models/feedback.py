"""
Recommendation feedback = the closed loop.

Every prioritization result and next-best-action recommendation gets a
`recommendation_id`. When the account manager acts (or doesn't), a feedback
row is written. The feedback service aggregates these and:
  1. Adjusts per-tenant rule weights (see services/prioritization_v2.py)
  2. Feeds a labeled dataset into the periodic ML retraining job
  3. Powers a "recommendation quality" dashboard for team leads
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TenantMixin, TimestampMixin, new_uuid


class Recommendation(Base, TenantMixin, TimestampMixin):
    """A single recommendation shown to a user. Persisted so feedback has a
    stable ID to reference and so we can reconstruct 'what did the model see
    when it made this suggestion' during audits."""
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_reco_tenant_seller", "tenant_id", "seller_id"),
        Index("ix_reco_tenant_user_time", "tenant_id", "shown_to_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    seller_id: Mapped[str] = mapped_column(String(64), nullable=False)
    shown_to_user_id: Mapped[str] = mapped_column(String(36), nullable=False)

    # e.g. "prioritize", "next_best_action", "email_generated"
    recommendation_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # The specific action recommended, e.g. "reduce_returns", "increase_ads"
    action: Mapped[str] = mapped_column(String(128), nullable=False)

    # Numeric score / confidence
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Human-readable "why" — snapshot at the moment of recommendation
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # Snapshot of the features that produced this recommendation.
    # Stored as text (JSON) so we can trace back exactly what the model saw.
    features_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which model version generated it — critical for A/B analysis and
    # for knowing which model to blame when feedback is negative.
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RecommendationFeedback(Base, TenantMixin, TimestampMixin):
    __tablename__ = "recommendation_feedback"
    __table_args__ = (
        Index("ix_feedback_tenant_time", "tenant_id", "created_at"),
        Index("ix_feedback_reco", "recommendation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    recommendation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recommendations.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)

    # Outcome enum (checked in service layer):
    # "accepted"      = user acted on it
    # "dismissed"     = user marked as not relevant
    # "acted_on"      = downstream signal detected (email sent, seller called)
    # "reported_wrong" = user flagged the recommendation as incorrect
    # "no_action"    = timed out with no interaction (set by nightly job)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)

    # Optional free-text reason from the user (for "reported_wrong")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # For "acted_on", when the action was detected
    action_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
