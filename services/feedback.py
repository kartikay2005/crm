"""
Feedback loop service.

Two entry points:
- `record_recommendation` — called by prioritization/NBA services when a
  recommendation is shown to a user. Returns a recommendation_id.
- `record_feedback` — called by the UI when the user acts, dismisses, or
  reports a recommendation. Also called by a nightly job that infers
  "acted_on" from downstream signals (email sent, seller contacted).

The aggregate function is called by a nightly job (or on-demand for the
recommendation quality dashboard). It updates LearnedWeight rows that the
next prioritization run will consume.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.logging_config import get_logger
from core.metrics import recommendations_generated_total, recommendation_feedback_total
from models.config import LearnedWeight
from models.feedback import Recommendation, RecommendationFeedback
from services.audit import audit_event

_log = get_logger(__name__)

VALID_OUTCOMES = {"accepted", "dismissed", "acted_on", "reported_wrong", "no_action"}
POSITIVE_OUTCOMES = {"accepted", "acted_on"}
NEGATIVE_OUTCOMES = {"dismissed", "reported_wrong"}


def record_recommendation(
    db: Session,
    *,
    seller_id: str,
    user_id: str,
    recommendation_type: str,
    action: str,
    reason: str,
    score: float | None,
    features: dict[str, Any] | None,
    model_version: str,
) -> str:
    """Persist a recommendation. Return its ID so the UI can reference it in
    subsequent feedback calls."""
    reco = Recommendation(
        seller_id=seller_id,
        shown_to_user_id=user_id,
        recommendation_type=recommendation_type,
        action=action,
        reason=reason,
        score=score,
        features_snapshot=json.dumps(features) if features is not None else None,
        model_version=model_version,
    )
    db.add(reco)
    db.flush()
    recommendations_generated_total.labels(action_type=action).inc()
    return reco.id


def record_feedback(
    db: Session,
    *,
    recommendation_id: str,
    user_id: str,
    outcome: str,
    note: str | None = None,
) -> None:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}")

    reco = db.get(Recommendation, recommendation_id)
    if reco is None:
        raise ValueError("recommendation not found")

    now = datetime.now(timezone.utc)
    fb = RecommendationFeedback(
        recommendation_id=recommendation_id,
        user_id=user_id,
        outcome=outcome,
        note=note,
        action_completed_at=now if outcome == "acted_on" else None,
    )
    db.add(fb)

    recommendation_feedback_total.labels(outcome=outcome).inc()
    audit_event(
        db, "recommendation.feedback",
        resource_type="recommendation", resource_id=recommendation_id,
        payload={"outcome": outcome, "note_len": len(note) if note else 0},
    )


def close_stale_recommendations(db: Session, tenant_id: str, days: int = 7) -> int:
    """Nightly job: mark recommendations with no feedback after N days as
    "no_action". This is a training signal too — a recommendation nobody
    engaged with was probably not compelling."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    unhandled = db.execute(
        select(Recommendation.id)
        .where(Recommendation.tenant_id == tenant_id)
        .where(Recommendation.created_at < cutoff)
        .outerjoin(RecommendationFeedback,
                   RecommendationFeedback.recommendation_id == Recommendation.id)
        .where(RecommendationFeedback.id.is_(None))
    ).scalars().all()

    for reco_id in unhandled:
        db.add(RecommendationFeedback(
            recommendation_id=reco_id,
            user_id="system",
            outcome="no_action",
        ))
    return len(unhandled)


def aggregate_and_update_weights(db: Session, tenant_id: str, lookback_days: int = 90) -> dict:
    """Nightly job. Aggregates recent feedback and updates LearnedWeight rows.

    Logic:
      For each (action, category) bucket:
        pos = count(accepted) + count(acted_on)
        neg = count(dismissed) + count(reported_wrong)
        ratio = pos / (pos + neg + smoothing)
        multiplier = clamp(0.5 + ratio, 0.5, 1.5)

    Requires at least MIN_SIGNALS observations before adjusting away from 1.0
    to avoid overreacting to small samples.
    """
    MIN_SIGNALS = 20
    SMOOTHING = 5

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # Join reco + feedback + seller category (features_snapshot['category'])
    rows = db.execute(
        select(
            Recommendation.action,
            Recommendation.features_snapshot,
            RecommendationFeedback.outcome,
        )
        .join(RecommendationFeedback,
              RecommendationFeedback.recommendation_id == Recommendation.id)
        .where(Recommendation.tenant_id == tenant_id)
        .where(RecommendationFeedback.created_at >= cutoff)
    ).all()

    buckets: dict[tuple[str, str | None], dict[str, int]] = defaultdict(
        lambda: {"pos": 0, "neg": 0}
    )
    for action, features_json, outcome in rows:
        category = None
        if features_json:
            try:
                category = (json.loads(features_json) or {}).get("category")
            except Exception:
                pass
        key = (action, category)
        if outcome in POSITIVE_OUTCOMES:
            buckets[key]["pos"] += 1
        elif outcome in NEGATIVE_OUTCOMES:
            buckets[key]["neg"] += 1

    updated = 0
    for (action, category), counts in buckets.items():
        total = counts["pos"] + counts["neg"]
        if total < MIN_SIGNALS:
            continue
        ratio = counts["pos"] / (total + SMOOTHING)
        # Map ratio in [0,1] to multiplier in [0.5, 1.5]
        multiplier = max(0.5, min(1.5, 0.5 + ratio))

        existing = db.execute(
            select(LearnedWeight)
            .where(LearnedWeight.tenant_id == tenant_id)
            .where(LearnedWeight.action == action)
            .where(LearnedWeight.category.is_(category) if category is None
                   else LearnedWeight.category == category)
        ).scalar_one_or_none()

        if existing is None:
            db.add(LearnedWeight(
                tenant_id=tenant_id, action=action, category=category,
                weight_multiplier=multiplier,
                positive_feedback_count=counts["pos"],
                negative_feedback_count=counts["neg"],
            ))
        else:
            existing.weight_multiplier = multiplier
            existing.positive_feedback_count = counts["pos"]
            existing.negative_feedback_count = counts["neg"]
        updated += 1

    _log.info("feedback.aggregated", tenant_id=tenant_id, buckets_updated=updated)
    return {"buckets_updated": updated, "buckets_seen": len(buckets)}


def build_training_dataset(db: Session, tenant_id: str, lookback_days: int = 180) -> list[dict]:
    """Build a labeled dataset from feedback for the periodic ML retrain job.
    Positive = accepted|acted_on, negative = dismissed|reported_wrong.
    no_action is excluded (it's ambiguous — could be a good reco the user
    just didn't get to)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = db.execute(
        select(Recommendation, RecommendationFeedback)
        .join(RecommendationFeedback,
              RecommendationFeedback.recommendation_id == Recommendation.id)
        .where(Recommendation.tenant_id == tenant_id)
        .where(RecommendationFeedback.created_at >= cutoff)
        .where(RecommendationFeedback.outcome.in_(POSITIVE_OUTCOMES | NEGATIVE_OUTCOMES))
    ).all()

    dataset = []
    for reco, fb in rows:
        if not reco.features_snapshot:
            continue
        try:
            features = json.loads(reco.features_snapshot)
        except Exception:
            continue
        dataset.append({
            "features": features,
            "action": reco.action,
            "label": 1 if fb.outcome in POSITIVE_OUTCOMES else 0,
        })
    return dataset
