"""
Centralized configuration. All secrets and environment-specific values are
externalized via environment variables (12-factor). Validated at startup so
misconfiguration fails fast rather than at first request.

For enterprise deployment, back this with Vault / AWS Secrets Manager / GCP
Secret Manager — the env vars can be populated by the platform's secrets
injection layer (K8s CSI driver, ECS task definition, etc.).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Environment ---------------------------------------------------------
    env: Literal["local", "dev", "staging", "prod"] = "local"
    app_name: str = "ai-crm-assistant"
    version: str = "2.0.0"
    debug: bool = False

    # --- Database ------------------------------------------------------------
    # Never default to SQLite in prod. Validator below enforces this.
    database_url: SecretStr = Field(
        default=SecretStr("postgresql+psycopg://crm:crm@localhost:5432/crm")
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 30
    database_echo: bool = False

    # --- Security / JWT ------------------------------------------------------
    # In prod this MUST be a cryptographically strong value from a secrets store.
    # Rotate quarterly; the JWT layer supports keyed rotation via `kid`.
    jwt_secret_key: SecretStr = Field(default=SecretStr("dev-only-change-me"))
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 15
    jwt_refresh_token_ttl_days: int = 14
    jwt_issuer: str = "ai-crm-assistant"

    # Argon2 parameters — OWASP 2024 recommended baseline. Tune based on
    # target auth latency (~50ms per hash on production hardware is typical).
    argon2_time_cost: int = 3
    argon2_memory_cost_kb: int = 64_000
    argon2_parallelism: int = 4

    # --- PII encryption ------------------------------------------------------
    # Fernet key (32 url-safe base64 bytes) for column-level encryption of
    # PII fields (seller contact emails, phone numbers, etc). Rotate with
    # MultiFernet — see core/crypto.py.
    field_encryption_key: SecretStr = Field(default=SecretStr(""))

    # --- Session / auth ------------------------------------------------------
    max_failed_login_attempts: int = 5
    lockout_duration_minutes: int = 15
    require_mfa_for_roles: list[str] = Field(default_factory=lambda: ["admin", "team_lead"])
    session_absolute_ttl_hours: int = 24

    # --- SSO / OIDC (optional) ----------------------------------------------
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str | None = None

    # --- Rate limiting -------------------------------------------------------
    rate_limit_per_minute_authenticated: int = 300
    rate_limit_per_minute_anonymous: int = 30
    redis_url: str | None = None  # required for distributed rate limiting

    # --- Observability -------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    otel_exporter_endpoint: str | None = None
    metrics_enabled: bool = True

    # --- CORS / security headers --------------------------------------------
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])

    # --- LLM (optional) ------------------------------------------------------
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    llm_request_timeout_seconds: int = 20
    llm_max_retries: int = 2

    # --- Feature flags -------------------------------------------------------
    enable_feedback_loop: bool = True
    enable_llm_features: bool = False  # opt-in even when key is present
    
    # --- Demo seeding (optional) ----------------------------------------------
    # Shared-secret token guarding POST /_seed-demo. Unset (the default)
    # means that endpoint always 404s — it's disabled unless you deliberately
    # opt in for a demo deployment.
    seed_token: SecretStr | None = None

    # --- Seller data source ---------------------------------------------------
    # "sql" (default, reads a canonical view in the primary DB), "rest"
    # (marketplace exposes sellers via API), or "csv" (dev/demo only).
    data_source_kind: Literal["sql", "rest", "csv"] = "sql"
    seller_api_base_url: str | None = None
    seller_api_key: SecretStr | None = None
    seller_csv_path: str = "data/sellers.csv"

    # --- Dataset Intelligence Engine -------------------------------------------
    dataset_upload_dir: str = "data/dataset_uploads"
    dataset_max_upload_mb: int = 200
    dataset_allowed_extensions: list[str] = Field(
        default_factory=lambda: [".csv", ".xlsx", ".xls", ".tsv", ".json"]
    )
    dataset_profile_sample_rows: int = 200_000  # cap for correlation/skew/kurtosis on huge files
    model_registry_dir: str = "model_registry"

    # ------------------------------------------------------------------------
    @field_validator("database_url")
    @classmethod
    def _no_sqlite_in_prod(cls, v: SecretStr, info) -> SecretStr:
        url = v.get_secret_value().lower()
        env = info.data.get("env", "local")
        if env in ("staging", "prod") and url.startswith("sqlite"):
            raise ValueError("SQLite is not permitted in staging/prod")
        return v

    @field_validator("jwt_secret_key")
    @classmethod
    def _strong_jwt_secret_in_prod(cls, v: SecretStr, info) -> SecretStr:
        env = info.data.get("env", "local")
        if env in ("staging", "prod"):
            secret = v.get_secret_value()
            if len(secret) < 32 or secret == "dev-only-change-me":
                raise ValueError("jwt_secret_key must be >= 32 chars and non-default in staging/prod")
        return v

    @field_validator("field_encryption_key")
    @classmethod
    def _encryption_key_required_in_prod(cls, v: SecretStr, info) -> SecretStr:
        env = info.data.get("env", "local")
        if env in ("staging", "prod") and not v.get_secret_value():
            raise ValueError("field_encryption_key is required in staging/prod")
        return v

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. Import this, not the Settings class directly,
    so tests can override via dependency injection."""
    return Settings()
