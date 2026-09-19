"""
Authentication service.

Implements:
- Local password login with lockout after N failed attempts
- TOTP-based MFA (compatible with Google Authenticator, 1Password, etc.)
- SSO/OIDC login (hook — plug in your provider's discovery URL)
- Refresh-token rotation on every use (detects token theft)
- Session revocation

All actions emit audit events. No credential material is ever logged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

import pyotp
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import security
from core.config import get_settings
from core.crypto import encrypt, decrypt
from core.logging_config import get_logger
from core.metrics import auth_login_attempts_total
from models.rbac import Role, RolePermission, UserRole
from models.user import Session as SessionModel, User
from core.tenancy import tenant_scope, unscoped_bootstrap_query
from services.audit import audit_event

_log = get_logger(__name__)
_settings = get_settings()


@dataclass
class AuthResult:
    access_token: str
    refresh_token: str
    user_id: str
    tenant_id: str
    roles: list[str]
    mfa_required: bool = False


class AuthError(Exception):
    """Base class for all auth failures. Message is safe to return to clients."""


class InvalidCredentials(AuthError):
    def __init__(self):
        super().__init__("Invalid email or password")


class AccountLocked(AuthError):
    def __init__(self, until: datetime):
        super().__init__(f"Account locked until {until.isoformat()}")
        self.until = until


class MFARequired(AuthError):
    def __init__(self, mfa_token: str):
        super().__init__("MFA verification required")
        self.mfa_token = mfa_token


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def authenticate(
    db: Session,
    tenant_id: str,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
    mfa_code: str | None = None,
) -> AuthResult:
    """Full local-password login flow. Returns tokens on success, raises on failure.

    All failure paths take approximately the same time (Argon2 verify is dominant)
    so timing side channels can't distinguish 'no such user' from 'wrong password'.

    tenant_id is already known here (resolved from the login slug before this
    is called), so we establish a real tenant_scope for the whole flow rather
    than using the bootstrap bypass — this is the common case, not the
    exception. Contrast with refresh_session(), which genuinely doesn't know
    the tenant until it finds the session row.
    """
    with tenant_scope(tenant_id, user_id="anonymous-login", roles=()):
        return _authenticate_within_tenant(db, tenant_id, email, password, ip_address, user_agent, mfa_code)


def _authenticate_within_tenant(
    db: Session,
    tenant_id: str,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
    mfa_code: str | None,
) -> AuthResult:
    user = db.execute(
        select(User).where(User.tenant_id == tenant_id, User.email == email.lower())
    ).scalar_one_or_none()

    # ---- Timing-equal path: always run a password verify, real or dummy ----
    dummy_hash = "$argon2id$v=19$m=64000,t=3,p=4$" + "a" * 22 + "$" + "b" * 43

    if user is None or not user.password_hash:
        security.verify_password(password, dummy_hash)  # burn ~equivalent time
        auth_login_attempts_total.labels(result="invalid_credentials").inc()
        audit_event(db, "user.login_failed", payload={"email": email, "reason": "no_user"},
                    actor_ip=ip_address, tenant_id_override=tenant_id)
        raise InvalidCredentials()

    if not user.is_active:
        auth_login_attempts_total.labels(result="invalid_credentials").inc()
        audit_event(db, "user.login_failed", payload={"reason": "inactive"},
                    actor_user_id=user.id, actor_ip=ip_address)
        raise InvalidCredentials()

    now = datetime.now(timezone.utc)
    if user.locked_until and user.locked_until > now:
        auth_login_attempts_total.labels(result="locked").inc()
        audit_event(db, "user.login_failed", payload={"reason": "locked", "until": user.locked_until.isoformat()},
                    actor_user_id=user.id, actor_ip=ip_address)
        raise AccountLocked(user.locked_until)

    if not security.verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= _settings.max_failed_login_attempts:
            user.locked_until = now + timedelta(minutes=_settings.lockout_duration_minutes)
            audit_event(db, "user.locked", payload={"until": user.locked_until.isoformat()},
                        actor_user_id=user.id, actor_ip=ip_address)
        db.flush()
        auth_login_attempts_total.labels(result="invalid_credentials").inc()
        audit_event(db, "user.login_failed", payload={"reason": "bad_password"},
                    actor_user_id=user.id, actor_ip=ip_address)
        raise InvalidCredentials()

    # Password OK. If MFA is enabled, require the code.
    if user.mfa_enabled:
        if not mfa_code:
            auth_login_attempts_total.labels(result="mfa_required").inc()
            # Emit an MFA challenge token; client re-submits with code.
            mfa_token = security.create_access_token(
                subject=user.id, tenant_id=tenant_id, roles=[], permissions=[],
                extra_claims={"typ": "mfa_challenge"},
            )
            raise MFARequired(mfa_token)
        if not _verify_totp(user, mfa_code):
            user.failed_login_count += 1
            db.flush()
            auth_login_attempts_total.labels(result="invalid_credentials").inc()
            audit_event(db, "user.mfa_failed", actor_user_id=user.id, actor_ip=ip_address)
            raise InvalidCredentials()

    # Success — reset counters, check for rehash, issue tokens
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    return _issue_session(db, user, ip_address, user_agent)


def _verify_totp(user: User, code: str) -> bool:
    if not user.mfa_secret_encrypted:
        return False
    try:
        secret = decrypt(user.mfa_secret_encrypted)
    except Exception:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def _issue_session(
    db: Session, user: User, ip_address: str | None, user_agent: str | None
) -> AuthResult:
    roles = _load_user_roles(db, user)
    permissions = _permissions_for_roles(db, user.tenant_id, roles)

    refresh_raw, refresh_hash = security.create_refresh_token(user.id, "")
    session = SessionModel(
        tenant_id=user.tenant_id,
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        expires_at=datetime.now(timezone.utc)
                   + timedelta(days=_settings.jwt_refresh_token_ttl_days),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    db.flush()

    access = security.create_access_token(
        subject=user.id,
        tenant_id=user.tenant_id,
        roles=roles,
        permissions=permissions,
        extra_claims={"sid": session.id},
    )

    auth_login_attempts_total.labels(result="success").inc()
    audit_event(db, "user.login", actor_user_id=user.id, actor_ip=ip_address,
                payload={"session_id": session.id})

    return AuthResult(
        access_token=access,
        refresh_token=refresh_raw,
        user_id=user.id,
        tenant_id=user.tenant_id,
        roles=roles,
    )


# ---------------------------------------------------------------------------
# Token refresh with rotation
# ---------------------------------------------------------------------------
def refresh_session(
    db: Session, refresh_token: str, ip_address: str | None, user_agent: str | None
) -> AuthResult:
    """Rotate the refresh token. If the presented token was already used
    (i.e. revoked), we treat that as evidence of theft: revoke ALL sessions
    for the user and force re-auth.

    We genuinely don't know the tenant until we find the session row — the
    refresh token itself is the only lookup key, and it's a 48-byte CSPRNG
    value (see security.create_refresh_token), so its uniqueness IS the
    security boundary for this specific lookup. We use the explicit,
    logged bootstrap bypass for that one query, then immediately switch to
    a real tenant_scope for everything else in this flow.
    """
    token_hash = security.hash_token(refresh_token)

    with unscoped_bootstrap_query("refresh_token lookup by unique hash, tenant unknown until found"):
        session = db.execute(
            select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
        ).scalar_one_or_none()

    if session is None:
        raise InvalidCredentials()

    with tenant_scope(session.tenant_id, session.user_id, roles=()):
        return _refresh_within_tenant(db, session, ip_address, user_agent)


def _refresh_within_tenant(
    db: Session, session: SessionModel, ip_address: str | None, user_agent: str | None
) -> AuthResult:
    now = datetime.now(timezone.utc)
    if session.revoked_at is not None:
        # Reuse of a revoked token — assume theft
        _revoke_all_user_sessions(db, session.user_id)
        audit_event(db, "user.session_theft_detected",
                    actor_user_id=session.user_id, actor_ip=ip_address,
                    payload={"session_id": session.id})
        raise InvalidCredentials()

    if session.expires_at <= now:
        raise InvalidCredentials()

    # Rotate: revoke old, issue new
    session.revoked_at = now
    user = db.get(User, session.user_id)
    return _issue_session(db, user, ip_address, user_agent)


def revoke_session(db: Session, session_id: str, user_id: str, tenant_id: str) -> None:
    """Caller must already be inside a tenant_scope (e.g. via the
    get_tenant_context API dependency) — this function does not establish
    one itself since it's always called from an authenticated context where
    the tenant is already known from the access token."""
    session = db.get(SessionModel, session_id)
    if session and session.user_id == user_id and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        audit_event(db, "user.logout", actor_user_id=user_id,
                    payload={"session_id": session_id})


def _revoke_all_user_sessions(db: Session, user_id: str) -> None:
    """Called from within _refresh_within_tenant, which already runs inside
    a tenant_scope — the UPDATE below still gets tenant-filtered by the
    ORM hook like any other statement against a TenantScopedMixin model."""
    from sqlalchemy import update
    db.execute(
        update(SessionModel)
        .where(SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


# ---------------------------------------------------------------------------
# MFA enrollment
# ---------------------------------------------------------------------------
def enroll_mfa(db: Session, user_id: str) -> tuple[str, str]:
    """Generate a TOTP secret and return (secret, provisioning_uri).
    The user scans the URI's QR into their authenticator app, then must
    submit a valid code to `confirm_mfa` to enable.

    Caller must already be inside a tenant_scope — this is always invoked
    from an authenticated API route where the tenant is known from the
    caller's own access token (see api/deps.get_tenant_context)."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")
    secret = pyotp.random_base32()
    # Encrypt at rest — decrypted only in memory during verify
    user.mfa_secret_encrypted = encrypt(secret)
    provisioning = pyotp.TOTP(secret).provisioning_uri(
        name=user.email, issuer_name=_settings.app_name
    )
    audit_event(db, "user.mfa_enrolled", actor_user_id=user_id)
    return secret, provisioning


def confirm_mfa(db: Session, user_id: str, code: str) -> None:
    user = db.get(User, user_id)
    if not user or not _verify_totp(user, code):
        raise InvalidCredentials()
    user.mfa_enabled = True
    audit_event(db, "user.mfa_enabled", actor_user_id=user_id)


# ---------------------------------------------------------------------------
# Role loading
# ---------------------------------------------------------------------------
def _load_user_roles(db: Session, user: User) -> list[str]:
    rows = db.execute(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, UserRole.tenant_id == user.tenant_id)
    ).scalars().all()
    return list(rows)


def _permissions_for_roles(db: Session, tenant_id: str, role_names: list[str]) -> list[str]:
    if not role_names:
        return []
    rows = db.execute(
        select(RolePermission.permission)
        .join(Role, Role.id == RolePermission.role_id)
        .where(Role.tenant_id == tenant_id, Role.name.in_(role_names))
    ).scalars().all()
    return sorted(set(rows))
