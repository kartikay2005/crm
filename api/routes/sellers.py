"""
Seller read endpoints. Backed by whichever SellerDataSource is configured
for this deployment (see services/data_source.py and api/dependencies_data.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies_data import get_seller_source
from api.deps import get_tenant_context, require_permission
from api.schemas import SellerListResponse, SellerOut
from models.rbac import Perm
from services.data_source import SellerDataSource

router = APIRouter(prefix="/sellers", tags=["sellers"])


@router.get("", response_model=SellerListResponse)
def list_sellers(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tenant_ctx=Depends(get_tenant_context),
    _perm=Depends(require_permission(Perm.SELLERS_READ)),
    source: SellerDataSource = Depends(get_seller_source),
):
    # NOTE: the SellerDataSource protocol as defined streams all matching
    # sellers; for true offset pagination at scale, extend adapters with
    # native LIMIT/OFFSET support. This reference impl paginates in-memory
    # for simplicity — acceptable for tenants up to ~50k sellers, revisit
    # for larger books.
    all_sellers = list(source.list_sellers(tenant_ctx.tenant_id))
    page = all_sellers[offset: offset + limit]
    return SellerListResponse(
        items=[SellerOut.model_validate(s) for s in page],
        total=len(all_sellers),
        limit=limit,
        offset=offset,
    )


@router.get("/{seller_id}", response_model=SellerOut)
def get_seller(
    seller_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _perm=Depends(require_permission(Perm.SELLERS_READ)),
    source: SellerDataSource = Depends(get_seller_source),
):
    seller = source.get_seller(tenant_ctx.tenant_id, seller_id)
    if seller is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seller not found")
    return SellerOut.model_validate(seller)
