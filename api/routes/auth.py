"""
Authentication endpoints.

Note: /login takes tenant_slug in the body rather than resolving tenant from
subdomain — this reference impl keeps routing simple. In production, prefer
resolving tenant from subdomain/custom-domain at the edge (load balancer or
API gateway) and injecting X-Tenant-Id, so the login form never needs to ask
"which company do you work for."
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_client_ip, get_current_claims, get_db, get_tenant_context
from api.schemas import (
    LoginRequest, MFAChallengeResponse, MFAConfirmRequest, MFAEnrollResponse,
    RefreshRequest, TokenResponse,
)
from core.config import get_settings
from models.tenant import Tenant
from services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
_settings = get_settings()


@router.post("/login", response_model=TokenResponse, responses={
    202: {"model": MFAChallengeResponse, "description": "MFA code required"},
})
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    tenant = db.execute(
        select(Tenant).where(Tenant.slug == body.tenant_slug, Tenant.is_active == True)  # noqa: E712
    ).scalar_one_or_none()
    if tenant is None:
        # Same error as bad credentials — don't leak tenant existence
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    try:
        result = auth_service.authenticate(
            db, tenant.id, body.email, body.password,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
            mfa_code=body.mfa_code,
        )
    except auth_service.MFARequired as e:
        return _mfa_challenge_response(e.mfa_token)
    except auth_service.AccountLocked as e:
        raise HTTPException(status.HTTP_423_LOCKED, str(e))
    except auth_service.InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in_minutes=_settings.jwt_access_token_ttl_minutes,
    )


def _mfa_challenge_response(mfa_token: str):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"mfa_required": True, "mfa_token": mfa_token},
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    try:
        result = auth_service.refresh_session(
            db, body.refresh_token,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )
    except auth_service.InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in_minutes=_settings.jwt_access_token_ttl_minutes,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    tenant_ctx=Depends(get_tenant_context),
    claims: dict = Depends(get_current_claims),
    db: Session = Depends(get_db),
):
    session_id = claims.get("sid")
    if session_id:
        auth_service.revoke_session(db, session_id, claims["sub"], tenant_ctx.tenant_id)


@router.post("/mfa/enroll", response_model=MFAEnrollResponse)
def mfa_enroll(
    tenant_ctx=Depends(get_tenant_context),
    claims: dict = Depends(get_current_claims),
    db: Session = Depends(get_db),
):
    secret, uri = auth_service.enroll_mfa(db, claims["sub"])
    return MFAEnrollResponse(secret=secret, provisioning_uri=uri)


@router.post("/mfa/confirm", status_code=status.HTTP_204_NO_CONTENT)
def mfa_confirm(
    body: MFAConfirmRequest,
    tenant_ctx=Depends(get_tenant_context),
    claims: dict = Depends(get_current_claims),
    db: Session = Depends(get_db),
):
    try:
        auth_service.confirm_mfa(db, claims["sub"], body.code)
    except auth_service.InvalidCredentials:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid MFA code")
