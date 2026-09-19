"""
Security primitives: password hashing, token generation, JWT sign/verify.

Design principles:
- Argon2id for password hashing (memory-hard, side-channel resistant)
- JWT with short-lived access + longer refresh, both revocable via session store
- All cryptographic comparisons in constant time
- All tokens generated with secrets.token_urlsafe (CSPRNG)

Do NOT roll your own crypto. This file wraps well-audited libraries.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from core.config import get_settings

_settings = get_settings()

_hasher = PasswordHasher(
    time_cost=_settings.argon2_time_cost,
    memory_cost=_settings.argon2_memory_cost_kb,
    parallelism=_settings.argon2_parallelism,
)


# -----------------------------------------------------------------------------
# Password hashing
# -----------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Argon2id hash. Salt is embedded in the returned string."""
    if not password or len(password) < 12:
        # Enforced at the API layer via pydantic, but defense-in-depth here.
        raise ValueError("Password must be at least 12 characters")
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verification. Returns False on any failure — never raises
    to callers, so attackers can't distinguish 'no such user' from 'wrong password'
    via exception timing."""
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """True if the hash was computed with weaker parameters than current config.
    Call after a successful login and re-hash if True to migrate old hashes."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except Exception:
        return True


# -----------------------------------------------------------------------------
# Token generation
# -----------------------------------------------------------------------------
def generate_token(nbytes: int = 32) -> str:
    """Cryptographically secure URL-safe token. Use for session IDs, password
    reset tokens, email verification codes, etc."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 hash of a token, for storing in the DB. We never store raw
    session/refresh tokens — only their hashes — so a DB dump doesn't
    grant impersonation."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    """Timing-safe string comparison. Use whenever comparing secrets."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# -----------------------------------------------------------------------------
# JWT
# -----------------------------------------------------------------------------
def create_access_token(
    subject: str,
    tenant_id: str,
    roles: list[str],
    permissions: list[str],
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Short-lived access token. Includes tenant + RBAC claims so the API
    layer can authorize without a DB round-trip on every request."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "tid": tenant_id,
        "roles": roles,
        "perms": permissions,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=_settings.jwt_access_token_ttl_minutes),
        "iss": _settings.jwt_issuer,
        "typ": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        _settings.jwt_secret_key.get_secret_value(),
        algorithm=_settings.jwt_algorithm,
    )


def create_refresh_token(subject: str, session_id: str) -> tuple[str, str]:
    """Returns (raw_token, hash). The raw is returned to the client once;
    only the hash is persisted. The session_id links back to the session row
    so we can revoke without decoding the token."""
    raw = generate_token(48)
    return raw, hash_token(raw)


def decode_token(token: str) -> dict[str, Any]:
    """Verify signature + expiry. Raises jwt.PyJWTError subclasses on failure.
    Callers should catch and translate to 401."""
    return jwt.decode(
        token,
        _settings.jwt_secret_key.get_secret_value(),
        algorithms=[_settings.jwt_algorithm],
        issuer=_settings.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "tid"]},
    )
