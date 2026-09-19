"""
Structured logging with request/tenant context automatically attached.

- JSON output in prod so log aggregation (Datadog, Splunk, ELK) can parse fields
- Console output in dev for readability
- Sensitive fields (passwords, tokens, PII) redacted via processor
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from core.config import get_settings

_settings = get_settings()

SENSITIVE_KEYS = {
    "password", "password_hash", "token", "access_token", "refresh_token",
    "jwt_secret_key", "authorization", "cookie", "set-cookie", "api_key",
    "openai_api_key", "field_encryption_key", "ssn", "credit_card",
}


def _redact_sensitive(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Redact any key whose name suggests a secret. Applied to nested dicts too."""

    def _walk(d):
        if isinstance(d, dict):
            return {
                k: ("***REDACTED***" if k.lower() in SENSITIVE_KEYS else _walk(v))
                for k, v in d.items()
            }
        if isinstance(d, list):
            return [_walk(x) for x in d]
        return d

    return _walk(event_dict)


def _add_tenant_context(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Attach current tenant/user IDs to every log line if a context is active.
    This makes cross-tenant support triage trivial."""
    from core.tenancy import current_tenant_or_none
    ctx = current_tenant_or_none()
    if ctx is not None:
        event_dict.setdefault("tenant_id", ctx.tenant_id)
        event_dict.setdefault("user_id", ctx.user_id)
    return event_dict


def configure_logging() -> None:
    """Call once at app startup."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=_settings.log_level,
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_tenant_context,
        _redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if _settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(_settings.log_level)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
