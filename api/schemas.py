"""
Pydantic schemas for request/response validation.

Every mutating endpoint validates input through these — no raw dict access
in route handlers. Field constraints (max_length, regex, etc.) double as a
first line of defense against injection and oversized payloads.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# --- Auth ---------------------------------------------------------------
class LoginRequest(BaseModel):
    tenant_slug: str = Field(..., max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)
    mfa_code: str | None = Field(None, min_length=6, max_length=6, pattern=r"^\d{6}$")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class MFAChallengeResponse(BaseModel):
    mfa_required: bool = True
    mfa_token: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=16, max_length=512)


class MFAEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MFAConfirmRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class SignupRequest(BaseModel):
    """Admin-invoked, not self-serve — see api/routes/admin.py."""
    email: EmailStr
    display_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=12, max_length=256)
    role_names: list[str] = Field(..., min_length=1, max_length=10)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        classes = sum([
            any(c.islower() for c in v),
            any(c.isupper() for c in v),
            any(c.isdigit() for c in v),
            any(not c.isalnum() for c in v),
        ])
        if classes < 3:
            raise ValueError(
                "Password must contain at least 3 of: lowercase, uppercase, digit, symbol"
            )
        return v


# --- Sellers --------------------------------------------------------------
class SellerOut(BaseModel):
    seller_id: str
    company_name: str
    category: str
    country: str
    region: str
    monthly_revenue: float
    growth_rate: float
    return_rate: float
    customer_rating: float
    seller_tier: str

    model_config = {"from_attributes": True}


class SellerListResponse(BaseModel):
    items: list[SellerOut]
    total: int
    limit: int
    offset: int


# --- Prioritization ---------------------------------------------------------
class PrioritizedSellerOut(BaseModel):
    seller_id: str
    company_name: str
    score: float
    tier: str
    top_reason: str
    all_reasons: list[str]
    recommendation_id: str | None


# --- Feedback ---------------------------------------------------------------
class FeedbackRequest(BaseModel):
    recommendation_id: str = Field(..., max_length=36)
    outcome: str = Field(..., pattern=r"^(accepted|dismissed|acted_on|reported_wrong)$")
    note: str | None = Field(None, max_length=2000)


# --- Admin: thresholds -------------------------------------------------------
class ThresholdUpsertRequest(BaseModel):
    category: str | None = Field(None, max_length=64)
    region: str | None = Field(None, max_length=64)
    weight_revenue: float = Field(0.20, ge=0, le=1)
    weight_growth: float = Field(0.20, ge=0, le=1)
    weight_returns: float = Field(0.15, ge=0, le=1)
    weight_shipping: float = Field(0.15, ge=0, le=1)
    weight_rating: float = Field(0.10, ge=0, le=1)
    weight_conversion: float = Field(0.10, ge=0, le=1)
    weight_support: float = Field(0.10, ge=0, le=1)
    high_return_rate: float = Field(0.12, ge=0, le=1)
    declining_growth_rate: float = Field(-0.03, ge=-1, le=0)
    late_shipment_threshold: float = Field(0.08, ge=0, le=1)
    low_rating_threshold: float = Field(4.0, ge=0, le=5)

    @field_validator("weight_support")
    @classmethod
    def _weights_sum_near_one(cls, v, info):
        keys = [
            "weight_revenue", "weight_growth", "weight_returns",
            "weight_shipping", "weight_rating", "weight_conversion",
        ]
        total = sum(info.data.get(k, 0) for k in keys) + v
        if not (0.9 <= total <= 1.1):
            raise ValueError(f"Weights must sum to ~1.0, got {total:.3f}")
        return v


# --- Dataset Intelligence Engine (Segment 1: upload + profile) -------------
class ColumnProfileOut(BaseModel):
    name: str
    dtype: str
    inferred_type: str
    missing_count: int
    missing_pct: float
    unique_count: int
    is_constant: bool
    is_target_candidate: bool
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None
    outlier_pct: float | None = None
    top_values: dict[str, int] | None = None
    cardinality_ratio: float | None = None


class DatasetProfileOut(BaseModel):
    dataset_name: str
    n_rows: int
    n_columns: int
    memory_usage_bytes: int
    duplicate_row_count: int
    duplicate_row_pct: float
    columns: list[ColumnProfileOut]
    numerical_columns: list[str]
    categorical_columns: list[str]
    datetime_columns: list[str]
    boolean_columns: list[str]
    constant_columns: list[str]
    target_column_candidates: list[str]
    correlation_matrix: dict[str, dict[str, float | None]] | None
    class_imbalance: dict | None
    sampled_for_stats: bool
    sample_size: int | None
    cleaning_notes: list[str]


class DomainScoreOut(BaseModel):
    domain: str
    confidence: float
    band: str
    component_scores: dict[str, float]
    matched_columns: list[str]


class ScoreBreakdownOut(BaseModel):
    score: float
    label: str
    components: dict[str, float]
    notes: list[str]


class SchemaMatchOut(BaseModel):
    model_name: str
    match_pct: float
    matched_required: list[str]
    missing_required: list[str]
    matched_optional: list[str]
    dtype_mismatches: list[str]
    missing_data_violations: list[str]


class ModelSelectionOut(BaseModel):
    selected_model_name: str | None
    selected_model_version: str | None
    schema_match_pct: float | None
    fallback_reason: str | None
    considered: list[SchemaMatchOut]


class RowPredictionOut(BaseModel):
    row_index: int
    prediction: float | int | str
    probability: float | None
    class_probabilities: dict[str, float] | None


class PredictionSummaryOut(BaseModel):
    """Lightweight — included directly in the upload response. Full
    row-level predictions and SHAP explanations live behind the dedicated
    /predictions endpoint to keep the upload response payload small."""
    model_name: str
    model_version: str
    problem_type: str
    target_variable: str
    n_rows_predicted: int
    rows_dropped_missing_features: int
    inference_time_seconds: float
    rows_per_second: float
    prediction_distribution: dict[str, int | float]
    top_global_features: dict[str, float]


class FeatureContributionOut(BaseModel):
    feature: str
    value: float
    shap_value: float
    direction: str


class LocalExplanationOut(BaseModel):
    row_index: int
    natural_language: str
    top_contributors: list[FeatureContributionOut]


class PredictionDetailOut(BaseModel):
    model_name: str
    model_version: str
    problem_type: str
    target_variable: str
    n_rows_predicted: int
    rows_dropped_missing_features: int
    inference_time_seconds: float
    rows_per_second: float
    prediction_distribution: dict[str, int | float]
    row_predictions: list[RowPredictionOut]
    explainer_type: str | None
    global_importance: dict[str, float]
    local_explanations: list[LocalExplanationOut]


class InsightOut(BaseModel):
    category: str
    text: str
    severity: str
    supporting_data: dict


class InsightBundleOut(BaseModel):
    summary: str
    key_findings: list[InsightOut]
    risks: list[InsightOut]
    hidden_trends: list[InsightOut]
    anomalies: list[InsightOut]


class RecommendationOut(BaseModel):
    text: str
    priority: str
    domain: str
    rationale: str


class InsightsSummaryOut(BaseModel):
    """Lightweight — included directly in the upload response. Full
    insight bundle + recommendations live behind dedicated endpoints,
    same pattern as predictions."""
    summary: str
    top_findings: list[str]
    top_recommendations: list[RecommendationOut]


class DatasetUploadResponse(BaseModel):
    dataset_id: str
    filename: str
    status: str
    profile: DatasetProfileOut
    top_domain: str
    domain_confidence: float
    domain_band: str
    domain_scores: list[DomainScoreOut]
    quality: ScoreBreakdownOut
    readiness: ScoreBreakdownOut
    model_selection: ModelSelectionOut
    prediction_summary: PredictionSummaryOut | None
    insights_summary: InsightsSummaryOut | None


class DatasetValidationErrorResponse(BaseModel):
    detail: str
    stage: str
    issues: list[str]


class ChartOut(BaseModel):
    chart_id: str
    type: str
    title: str
    data: dict


class DatasetListItemOut(BaseModel):
    dataset_id: str
    filename: str
    status: str
    top_domain: str | None
    uploaded_at: datetime


class DatasetListResponse(BaseModel):
    items: list[DatasetListItemOut]
    total: int
    limit: int
    offset: int


class RepredictResponse(BaseModel):
    dataset_id: str
    status: str
    model_selection: ModelSelectionOut
    prediction_summary: PredictionSummaryOut | None


class ModelInfoOut(BaseModel):
    name: str
    domain: str
    version: str
    problem_type: str
    target_variable: str
    accuracy: float | None
    artifact_available: bool
    description: str


class DomainInfoOut(BaseModel):
    name: str
    example_signature_columns: list[str]


# --- Health -------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
