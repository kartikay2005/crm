"""
Tunable configuration for the prioritization/health/next-best-action engines.

The demo used hard-coded thresholds (e.g. return_rate > 0.12 = high risk).
In production these vary by category (electronics returns are normally higher
than groceries) and region (shipping SLAs differ by country).

This module stores those thresholds as tenant + category + region tuples, with
inheritance: category-specific overrides tenant default, region overrides category.

Team leads with `config:write` can edit these via the admin API without a
code deploy.
"""
from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TenantMixin, TimestampMixin, new_uuid

# Single source of truth for "no threshold row configured yet" defaults.
#
# IMPORTANT SQLAlchemy footgun this exists to fix: `mapped_column(default=X)`
# only applies X when the ORM actually flushes an INSERT — it does NOT
# populate the attribute on a freshly-constructed, never-flushed Python
# instance. `CategoryThreshold(tenant_id=t)` on its own leaves every other
# field as None, not its documented default. This bit
# services/prioritization_v2.py's "ultimate fallback" path (used whenever a
# tenant hasn't configured any thresholds yet — true for every new tenant by
# default): it constructed exactly that kind of bare, unflushed instance and
# then multiplied its None weight fields against a float, crashing
# GET /prioritization with a 500 on every request for any tenant without
# pre-existing threshold rows. Both the column defaults below AND that
# fallback path now build from this one dict so they can't diverge again.
DEFAULT_THRESHOLD_VALUES: dict = {
    "weight_revenue": 0.20,
    "weight_growth": 0.20,
    "weight_returns": 0.15,
    "weight_shipping": 0.15,
    "weight_rating": 0.10,
    "weight_conversion": 0.10,
    "weight_support": 0.10,
    "high_return_rate": 0.12,
    "declining_growth_rate": -0.03,
    "late_shipment_threshold": 0.08,
    "low_rating_threshold": 4.0,
    "contact_cadence_healthy_days": 30,
    "contact_cadence_at_risk_days": 7,
    "contact_cadence_critical_days": 2,
}


class CategoryThreshold(Base, TenantMixin, TimestampMixin):
    """Thresholds for one (tenant, category, region) tuple.

    A NULL category means 'tenant default across categories'.
    A NULL region means 'applies to all regions for this category'.

    Lookup: prefer most specific match (category+region), fall back to
    category, fall back to tenant default.
    """
    __tablename__ = "category_thresholds"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "category", "region", name="uq_thresholds_tenant_cat_region"
        ),
        Index("ix_thresholds_lookup", "tenant_id", "category", "region"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Health score component weights (must sum to ~1.0, validated on write)
    weight_revenue: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["weight_revenue"], nullable=False)
    weight_growth: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["weight_growth"], nullable=False)
    weight_returns: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["weight_returns"], nullable=False)
    weight_shipping: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["weight_shipping"], nullable=False)
    weight_rating: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["weight_rating"], nullable=False)
    weight_conversion: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["weight_conversion"], nullable=False)
    weight_support: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["weight_support"], nullable=False)

    # Risk thresholds
    high_return_rate: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["high_return_rate"], nullable=False)
    declining_growth_rate: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["declining_growth_rate"], nullable=False)
    late_shipment_threshold: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["late_shipment_threshold"], nullable=False)
    low_rating_threshold: Mapped[float] = mapped_column(Float, default=DEFAULT_THRESHOLD_VALUES["low_rating_threshold"], nullable=False)

    # Follow-up cadence (days between contacts)
    contact_cadence_healthy_days: Mapped[int] = mapped_column(Integer, default=DEFAULT_THRESHOLD_VALUES["contact_cadence_healthy_days"], nullable=False)
    contact_cadence_at_risk_days: Mapped[int] = mapped_column(Integer, default=DEFAULT_THRESHOLD_VALUES["contact_cadence_at_risk_days"], nullable=False)
    contact_cadence_critical_days: Mapped[int] = mapped_column(Integer, default=DEFAULT_THRESHOLD_VALUES["contact_cadence_critical_days"], nullable=False)


class LearnedWeight(Base, TenantMixin, TimestampMixin):
    """Weights learned from feedback. Applied AFTER category thresholds to
    adjust the recommendation score based on what actually worked for this
    tenant historically.

    Updated by the nightly feedback aggregation job."""
    __tablename__ = "learned_weights"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "action", "category", name="uq_learned_tenant_action_cat"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Multiplier applied to base score. Bounded [0.5, 1.5] to prevent runaway.
    weight_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Signal count for confidence in the learned weight
    positive_feedback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    negative_feedback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
