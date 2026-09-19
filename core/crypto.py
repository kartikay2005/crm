"""
Column-level encryption for PII fields (seller contact email, phone, tax IDs).

Uses Fernet (AES-128-CBC + HMAC-SHA256). MultiFernet supports key rotation:
- Add the new key to the front of the list
- Old ciphertexts still decrypt with the old key
- Re-encrypt on next write to migrate forward

For enterprise KMS integration (AWS KMS, GCP KMS, Azure Key Vault), replace
`_load_keys` with a KMS-backed data-encryption-key envelope pattern.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet

from core.config import get_settings


def _load_keys() -> MultiFernet:
    settings = get_settings()
    raw = settings.field_encryption_key.get_secret_value()
    if not raw:
        # Return a no-op wrapper that raises on use — we don't want silent
        # plaintext writes if the key is misconfigured.
        raise RuntimeError("field_encryption_key not configured")
    keys = [Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()]
    return MultiFernet(keys)


_fernet: MultiFernet | None = None


def _get_fernet() -> MultiFernet:
    global _fernet
    if _fernet is None:
        _fernet = _load_keys()
    return _fernet


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        return None  # type: ignore
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    if ciphertext is None:
        return None  # type: ignore
    return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


def rotate(ciphertext: str) -> str:
    """Re-encrypt with the current primary key. Call from a background job to
    migrate ciphertext after a key rotation."""
    return _get_fernet().rotate(ciphertext.encode("ascii")).decode("ascii")
