"""
Prioritization endpoint — the "who should I call today" view.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.dependencies_data import get_seller_source
from api.deps import get_db, get_tenant_context, require_permission
from api.schemas import PrioritizedSellerOut
from models.rbac import Perm
from services.data_source import SellerDataSource
from services.prioritization_v2 import prioritize

router = APIRouter(prefix="/prioritization", tags=["prioritization"])


@router.get("", response_model=list[PrioritizedSellerOut])
def get_prioritization(
    limit: int = Query(50, ge=1, le=200),
    tenant_ctx=Depends(get_tenant_context),
    claims: dict = Depends(require_permission(Perm.RECOMMENDATIONS_READ)),
    db: Session = Depends(get_db),
    source: SellerDataSource = Depends(get_seller_source),
):
    results = prioritize(
        db, source, tenant_ctx.tenant_id, claims["sub"], limit=limit,
    )
    return [PrioritizedSellerOut(**r.__dict__) for r in results]
