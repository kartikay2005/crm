"""
Admin endpoints — threshold configuration and user/role management.
Everything here requires CONFIG_WRITE or USERS_MANAGE, held only by
tenant_admin by default (see models/rbac.py DEFAULT_ROLE_PERMISSIONS).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db, get_tenant_context, require_permission
from api.schemas import SignupRequest, ThresholdUpsertRequest
from core.security import hash_password
from models.config import CategoryThreshold
from models.rbac import Perm, Role, UserRole
from models.user import User
from services.audit import audit_event

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Threshold configuration -------------------------------------------------
@router.put("/thresholds", status_code=status.HTTP_204_NO_CONTENT)
def upsert_threshold(
    body: ThresholdUpsertRequest,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.CONFIG_WRITE)),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(CategoryThreshold)
        .where(CategoryThreshold.tenant_id == tenant_ctx.tenant_id)
        .where(CategoryThreshold.category == body.category if body.category
               else CategoryThreshold.category.is_(None))
        .where(CategoryThreshold.region == body.region if body.region
               else CategoryThreshold.region.is_(None))
    ).scalar_one_or_none()

    fields = body.model_dump(exclude={"category", "region"})
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        row = existing
    else:
        row = CategoryThreshold(category=body.category, region=body.region, **fields)
        db.add(row)
    db.flush()

    audit_event(
        db, "config.threshold_updated",
        resource_type="category_threshold", resource_id=row.id,
        payload={"category": body.category, "region": body.region},
    )


@router.get("/thresholds", response_model=list[ThresholdUpsertRequest])
def list_thresholds(
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.CONFIG_READ)),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(CategoryThreshold).where(CategoryThreshold.tenant_id == tenant_ctx.tenant_id)
    ).scalars().all()
    return [ThresholdUpsertRequest(**{
        k: getattr(r, k) for k in ThresholdUpsertRequest.model_fields
    }) for r in rows]


# --- User management ----------------------------------------------------------
@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: SignupRequest,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.USERS_MANAGE)),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(User).where(User.tenant_id == tenant_ctx.tenant_id, User.email == body.email.lower())
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "User with this email already exists")

    roles = db.execute(
        select(Role).where(Role.tenant_id == tenant_ctx.tenant_id, Role.name.in_(body.role_names))
    ).scalars().all()
    if len(roles) != len(set(body.role_names)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more roles not found")

    user = User(
        tenant_id=tenant_ctx.tenant_id,
        email=body.email.lower(),
        display_name=body.display_name,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.flush()

    for role in roles:
        db.add(UserRole(tenant_id=tenant_ctx.tenant_id, user_id=user.id, role_id=role.id))

    audit_event(
        db, "user.created", resource_type="user", resource_id=user.id,
        payload={"roles": body.role_names},
    )
    return {"user_id": user.id}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_user(
    user_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.USERS_MANAGE)),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant_ctx.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.is_active = False
    audit_event(db, "user.deactivated", resource_type="user", resource_id=user_id)
