"""
Prioritization v2 — category-aware, region-aware, and feedback-adaptive.

Improvements over the demo:
1. Thresholds resolved per (tenant, category, region) from CategoryThreshold
2. LearnedWeight multipliers applied on top of base scores
3. Every ranked recommendation is persisted with its feature snapshot for
   audit/retraining
4. Deterministic tie-breaking so the same inputs always produce the same order

Signature is intentionally similar to the original so callers can swap in.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.logging_config import get_logger
from models.config import CategoryThreshold, DEFAULT_THRESHOLD_VALUES, LearnedWeight
from services.data_source import SellerDataSource, SellerRecord
from services.feedback import record_recommendation

_log = get_logger(__name__)

MODEL_VERSION = "prioritization/v2.0.0"


@dataclass
class PrioritizedSeller:
    seller_id: str
    company_name: str
    score: float
    tier: str  # excellent | healthy | at_risk | critical
    top_reason: str
    all_reasons: list[str]
    recommendation_id: str | None = None


def _resolve_thresholds(
    db: Session, tenant_id: str, category: str, region: str
) -> CategoryThreshold:
    """Most-specific match wins: (cat, region) > (cat, None) > (None, None)."""
    for cat_val, reg_val in [(category, region), (category, None), (None, None)]:
        conditions = [CategoryThreshold.tenant_id == tenant_id]
        conditions.append(
            CategoryThreshold.category == cat_val if cat_val
            else CategoryThreshold.category.is_(None)
        )
        conditions.append(
            CategoryThreshold.region == reg_val if reg_val
            else CategoryThreshold.region.is_(None)
        )
        row = db.execute(select(CategoryThreshold).where(*conditions)).scalar_one_or_none()
        if row:
            return row

    # Ultimate fallback — no threshold row configured for this tenant at
    # any specificity level. Explicitly pass the documented defaults rather
    # than relying on the ORM's mapped_column(default=...), which only
    # applies at flush/INSERT time and leaves every field None on a bare,
    # never-persisted instance like this one (see DEFAULT_THRESHOLD_VALUES'
    # docstring in models/config.py — this is exactly the case it exists
    # to prevent regressing on).
    return CategoryThreshold(tenant_id=tenant_id, **DEFAULT_THRESHOLD_VALUES)


def _load_learned_multipliers(db: Session, tenant_id: str) -> dict[tuple[str, str | None], float]:
    rows = db.execute(
        select(LearnedWeight).where(LearnedWeight.tenant_id == tenant_id)
    ).scalars().all()
    return {(r.action, r.category): r.weight_multiplier for r in rows}


def _score_seller(seller: SellerRecord, t: CategoryThreshold) -> tuple[float, list[str]]:
    """Return (health_score_0_to_100, reasons). Higher = healthier."""
    reasons: list[str] = []
    score = 0.0

    # Revenue: normalize against a nominal target ($50k/mo). Capped at 1.0.
    rev_norm = min(seller.monthly_revenue / 50_000.0, 1.0)
    score += t.weight_revenue * rev_norm * 100

    # Growth: -0.1 -> 0.0, +0.1 -> 1.0, clamped
    growth_norm = max(0.0, min(1.0, (seller.growth_rate + 0.1) / 0.2))
    score += t.weight_growth * growth_norm * 100
    if seller.growth_rate < t.declining_growth_rate:
        reasons.append(f"Declining growth {seller.growth_rate:+.1%}")

    # Returns (inverse)
    return_norm = max(0.0, 1.0 - (seller.return_rate / max(t.high_return_rate, 0.01)))
    score += t.weight_returns * return_norm * 100
    if seller.return_rate > t.high_return_rate:
        reasons.append(f"High return rate {seller.return_rate:.1%}")

    # Shipping (inverse)
    ship_norm = max(0.0, 1.0 - (seller.late_shipment_pct / max(t.late_shipment_threshold, 0.01)))
    score += t.weight_shipping * ship_norm * 100
    if seller.late_shipment_pct > t.late_shipment_threshold:
        reasons.append(f"Late shipments {seller.late_shipment_pct:.1%}")

    # Rating (5-point scale; ≥4.5 = 1.0, ≤3.0 = 0.0)
    rating_norm = max(0.0, min(1.0, (seller.customer_rating - 3.0) / 1.5))
    score += t.weight_rating * rating_norm * 100
    if seller.customer_rating < t.low_rating_threshold:
        reasons.append(f"Rating {seller.customer_rating:.1f} below target")

    # Conversion (assume 5% is excellent, 1% is poor)
    conv_norm = max(0.0, min(1.0, (seller.conversion_rate - 0.01) / 0.04))
    score += t.weight_conversion * conv_norm * 100

    # Support load (fewer is better; 5+ tickets/mo = worst)
    support_norm = max(0.0, 1.0 - seller.support_tickets / 5.0)
    score += t.weight_support * support_norm * 100
    if seller.support_tickets >= 5:
        reasons.append(f"{seller.support_tickets} open support tickets")

    return round(score, 2), reasons


def _tier(score: float) -> str:
    if score >= 80:
        return "excellent"
    if score >= 65:
        return "healthy"
    if score >= 45:
        return "at_risk"
    return "critical"


def prioritize(
    db: Session,
    source: SellerDataSource,
    tenant_id: str,
    user_id: str,
    *,
    limit: int = 50,
    persist_recommendations: bool = True,
) -> list[PrioritizedSeller]:
    """Rank sellers by (100 - health_score) × learned_multiplier so the WORST
    (most-needing-attention) rise to the top, adjusted by what has historically
    worked for this tenant."""
    learned = _load_learned_multipliers(db, tenant_id)
    results: list[PrioritizedSeller] = []

    for seller in source.list_sellers(tenant_id):
        thresholds = _resolve_thresholds(db, tenant_id, seller.category, seller.region)
        health, reasons = _score_seller(seller, thresholds)
        tier = _tier(health)

        # Urgency = 100 - health, then multiplied by learned weight for "contact_seller"
        urgency = 100.0 - health
        mult = learned.get(("contact_seller", seller.category)) \
               or learned.get(("contact_seller", None)) \
               or 1.0
        adjusted = urgency * mult

        top_reason = reasons[0] if reasons else f"Health score {health:.0f}/100"

        reco_id = None
        if persist_recommendations and tier in ("at_risk", "critical"):
            reco_id = record_recommendation(
                db,
                seller_id=seller.seller_id,
                user_id=user_id,
                recommendation_type="prioritize",
                action="contact_seller",
                reason=top_reason,
                score=adjusted,
                features={
                    "category": seller.category,
                    "region": seller.region,
                    "monthly_revenue": seller.monthly_revenue,
                    "growth_rate": seller.growth_rate,
                    "return_rate": seller.return_rate,
                    "late_shipment_pct": seller.late_shipment_pct,
                    "rating": seller.customer_rating,
                    "support_tickets": seller.support_tickets,
                    "health_score": health,
                    "tier": tier,
                },
                model_version=MODEL_VERSION,
            )

        results.append(PrioritizedSeller(
            seller_id=seller.seller_id,
            company_name=seller.company_name,
            score=adjusted,
            tier=tier,
            top_reason=top_reason,
            all_reasons=reasons,
            recommendation_id=reco_id,
        ))

    # Deterministic tie-break: score desc, then seller_id asc
    results.sort(key=lambda r: (-r.score, r.seller_id))
    return results[:limit]
