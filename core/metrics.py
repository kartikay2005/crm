"""
Prometheus metrics. Exposed at /metrics by the API layer.

Cardinality guidance:
- Never use tenant_id or user_id as a label — high-cardinality labels explode
  Prometheus memory. Use structured logs for per-tenant analysis instead.
- Route labels use the templated path ('/sellers/{id}'), never the raw path.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REGISTRY_NAMESPACE = "crm"

# HTTP -----------------------------------------------------------------------
http_requests_total = Counter(
    f"{REGISTRY_NAMESPACE}_http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status"],
)

http_request_duration_seconds = Histogram(
    f"{REGISTRY_NAMESPACE}_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Auth -----------------------------------------------------------------------
auth_login_attempts_total = Counter(
    f"{REGISTRY_NAMESPACE}_auth_login_attempts_total",
    "Login attempts",
    ["result"],  # success|invalid_credentials|locked|mfa_required
)

auth_active_sessions = Gauge(
    f"{REGISTRY_NAMESPACE}_auth_active_sessions",
    "Currently active sessions (sampled)",
)

# Business logic -------------------------------------------------------------
recommendations_generated_total = Counter(
    f"{REGISTRY_NAMESPACE}_recommendations_generated_total",
    "Next-best-action recommendations generated",
    ["action_type"],
)

recommendation_feedback_total = Counter(
    f"{REGISTRY_NAMESPACE}_recommendation_feedback_total",
    "Feedback events on recommendations",
    ["outcome"],  # accepted|dismissed|acted_on|reported_wrong
)

# LLM ------------------------------------------------------------------------
llm_requests_total = Counter(
    f"{REGISTRY_NAMESPACE}_llm_requests_total",
    "LLM API calls",
    ["model", "result"],  # result: success|fallback|error
)

llm_request_duration_seconds = Histogram(
    f"{REGISTRY_NAMESPACE}_llm_request_duration_seconds",
    "LLM call latency",
    ["model"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0),
)

# ML models ------------------------------------------------------------------
model_predictions_total = Counter(
    f"{REGISTRY_NAMESPACE}_model_predictions_total",
    "Predictions served",
    ["model_name", "model_version"],
)
