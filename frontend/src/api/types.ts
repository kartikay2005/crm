// Mirrors backend Pydantic schemas in api/schemas.py. Kept in MANUAL sync —
// see IMPLEMENTATION_PLAN.md Segment 9 for the noted tradeoff (no codegen
// tooling wired up yet; a future improvement would generate these from
// FastAPI's OpenAPI schema via `openapi-typescript`).

export interface ColumnProfile {
  name: string;
  dtype: string;
  inferred_type: string;
  missing_count: number;
  missing_pct: number;
  unique_count: number;
  is_constant: boolean;
  is_target_candidate: boolean;
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  skewness: number | null;
  kurtosis: number | null;
  outlier_pct: number | null;
  top_values: Record<string, number> | null;
  cardinality_ratio: number | null;
}

export interface DatasetProfile {
  dataset_name: string;
  n_rows: number;
  n_columns: number;
  memory_usage_bytes: number;
  duplicate_row_count: number;
  duplicate_row_pct: number;
  columns: ColumnProfile[];
  numerical_columns: string[];
  categorical_columns: string[];
  datetime_columns: string[];
  boolean_columns: string[];
  constant_columns: string[];
  target_column_candidates: string[];
  correlation_matrix: Record<string, Record<string, number | null>> | null;
  class_imbalance: Record<string, unknown> | null;
  sampled_for_stats: boolean;
  sample_size: number | null;
  cleaning_notes: string[];
}

export interface DomainScore {
  domain: string;
  confidence: number;
  band: "green" | "yellow" | "red";
  component_scores: Record<string, number>;
  matched_columns: string[];
}

export interface ScoreBreakdown {
  score: number;
  label: string;
  components: Record<string, number>;
  notes: string[];
}

export interface SchemaMatch {
  model_name: string;
  match_pct: number;
  matched_required: string[];
  missing_required: string[];
  matched_optional: string[];
  dtype_mismatches: string[];
  missing_data_violations: string[];
}

export interface ModelSelection {
  selected_model_name: string | null;
  selected_model_version: string | null;
  schema_match_pct: number | null;
  fallback_reason: string | null;
  considered: SchemaMatch[];
}

export interface PredictionSummary {
  model_name: string;
  model_version: string;
  problem_type: string;
  target_variable: string;
  n_rows_predicted: number;
  rows_dropped_missing_features: number;
  inference_time_seconds: number;
  rows_per_second: number;
  prediction_distribution: Record<string, number>;
  top_global_features: Record<string, number>;
}

export interface Recommendation {
  text: string;
  priority: "high" | "medium" | "low";
  domain: string;
  rationale: string;
}

export interface InsightsSummary {
  summary: string;
  top_findings: string[];
  top_recommendations: Recommendation[];
}

export interface Insight {
  category: string;
  text: string;
  severity: "info" | "warning" | "critical";
  supporting_data: Record<string, unknown>;
}

export interface InsightBundle {
  summary: string;
  key_findings: Insight[];
  risks: Insight[];
  hidden_trends: Insight[];
  anomalies: Insight[];
}

export interface DatasetUploadResponse {
  dataset_id: string;
  filename: string;
  status: string;
  profile: DatasetProfile;
  top_domain: string;
  domain_confidence: number;
  domain_band: "green" | "yellow" | "red";
  domain_scores: DomainScore[];
  quality: ScoreBreakdown;
  readiness: ScoreBreakdown;
  model_selection: ModelSelection;
  prediction_summary: PredictionSummary | null;
  insights_summary: InsightsSummary | null;
}

export interface RowPrediction {
  row_index: number;
  prediction: number | string;
  probability: number | null;
  class_probabilities: Record<string, number> | null;
}

export interface FeatureContribution {
  feature: string;
  value: number;
  shap_value: number;
  direction: "increases" | "decreases";
}

export interface LocalExplanation {
  row_index: number;
  natural_language: string;
  top_contributors: FeatureContribution[];
}

export interface PredictionDetail {
  dataset_id: string;
  model_name: string;
  model_version: string;
  problem_type: string;
  target_variable: string;
  n_rows_predicted: number;
  rows_dropped_missing_features: number;
  inference_time_seconds: number;
  rows_per_second: number;
  prediction_distribution: Record<string, number>;
  row_predictions: RowPrediction[];
  explainer_type: string | null;
  global_importance: Record<string, number>;
  local_explanations: LocalExplanation[];
  explainability_error: string | null;
}

export interface Chart {
  chart_id: string;
  type: "bar" | "histogram" | "scatter" | "heatmap" | "pie" | "line" | "box";
  title: string;
  data: Record<string, unknown>;
}

export interface DatasetListItem {
  dataset_id: string;
  filename: string;
  status: string;
  top_domain: string | null;
  uploaded_at: string;
}

export interface DatasetListResponse {
  items: DatasetListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ModelInfo {
  name: string;
  domain: string;
  version: string;
  problem_type: string;
  target_variable: string;
  accuracy: number | null;
  artifact_available: boolean;
  description: string;
}

export interface DomainInfo {
  name: string;
  example_signature_columns: string[];
}

export interface LoginRequest {
  tenant_slug: string;
  email: string;
  password: string;
  mfa_code?: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in_minutes: number;
}
