"""
Feedback endpoint — the write side of the closed loop. Every call here feeds
the nightly aggregation job (services.feedback.aggregate_and_update_weights)
that adjusts LearnedWeight rows consumed by the next prioritization run.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.deps import get_db, get_tenant_context, require_permission
from api.schemas import FeedbackRequest
from models.rbac import Perm
from services import feedback as feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
def submit_feedback(
    body: FeedbackRequest,
    tenant_ctx=Depends(get_tenant_context),
    claims: dict = Depends(require_permission(Perm.RECOMMENDATIONS_FEEDBACK)),
    db: Session = Depends(get_db),
):
    try:
        feedback_service.record_feedback(
            db,
            recommendation_id=body.recommendation_id,
            user_id=claims["sub"],
            outcome=body.outcome,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
