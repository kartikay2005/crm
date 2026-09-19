"""
Dataset Intelligence Engine — Steps 5-6: Dataset Quality Score + AI Readiness Score.

Both are 0-100 composite scores built from signals already present in the
DatasetProfile (Step 2) and DomainDetectionResult (Steps 3-4) — no new data
scanning happens here, this is pure aggregation/weighting, which keeps it
cheap even on large datasets.

Quality Score answers: "is this data clean?"
AI Readiness Score answers: "can I trust a prediction made from this data?"
They're deliberately separate — a dataset can be spotlessly clean (high
quality) but still unsuitable for prediction (no clear target column, or
domain confidence too low to trust a schema match), and vice versa.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.dataset_intelligence.domain_detection import DomainDetectionResult
from services.dataset_intelligence.profiler import DatasetProfile

# --- Quality Score weights (spec lists these 8 signals; weighted evenly in
# two groups — "how much of the data is missing/wrong" vs "how consistent/
# well-formed is what's there" — see _quality_label for banding) ----------
_QUALITY_WEIGHTS = {
    "missing_values": 0.20,
    "duplicate_rows": 0.15,
    "outliers": 0.15,
    "feature_completeness": 0.15,  # inverse of constant-column ratio
    "null_percentage": 0.15,       # dataset-wide, distinct from per-column missing_values
    "datatype_consistency": 0.10,  # how much cleaning/coercion was needed
    "class_imbalance": 0.10,
}

_READINESS_WEIGHTS = {
    "feature_quality": 0.25,       # reuses the quality score
    "missing_values": 0.15,
    "domain_confidence": 0.20,
    "schema_match": 0.15,          # proxied by domain schema_completeness until
                                    # Segment 3's real model registry exists
    "training_compatibility": 0.15,  # row count adequacy
    "target_availability": 0.10,
}


@dataclass
class ScoreBreakdown:
    score: float  # 0-100
    label: str  # "Excellent" | "Good" | "Fair" | "Poor"
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _label(score: float) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Poor"


def compute_quality_score(profile: DatasetProfile) -> ScoreBreakdown:
    notes: list[str] = []

    avg_missing_pct = (
        sum(c.missing_pct for c in profile.columns) / len(profile.columns)
        if profile.columns else 0.0
    )
    missing_component = max(0.0, 100 - avg_missing_pct * 2)  # 50% avg missing -> 0
    if avg_missing_pct > 10:
        notes.append(f"Average missingness across columns is {avg_missing_pct:.1f}%.")

    dup_component = max(0.0, 100 - profile.duplicate_row_pct * 4)  # 25% dupes -> 0
    if profile.duplicate_row_pct > 5:
        notes.append(f"{profile.duplicate_row_pct:.1f}% of rows are exact duplicates.")

    outlier_pcts = [c.outlier_pct for c in profile.columns if c.outlier_pct is not None]
    avg_outlier_pct = sum(outlier_pcts) / len(outlier_pcts) if outlier_pcts else 0.0
    outlier_component = max(0.0, 100 - avg_outlier_pct * 3)  # ~33% outliers -> 0
    if avg_outlier_pct > 10:
        notes.append(f"Numerical columns average {avg_outlier_pct:.1f}% outliers (IQR method).")

    constant_ratio = len(profile.constant_columns) / max(profile.n_columns, 1)
    completeness_component = max(0.0, 100 - constant_ratio * 200)  # 50% constant cols -> 0
    if profile.constant_columns:
        notes.append(f"{len(profile.constant_columns)} column(s) have only one distinct value: "
                     f"{', '.join(profile.constant_columns[:5])}.")

    total_cells = profile.n_rows * profile.n_columns
    total_missing = sum(c.missing_count for c in profile.columns)
    null_pct = 100 * total_missing / total_cells if total_cells else 0.0
    null_component = max(0.0, 100 - null_pct * 2)

    coerced = len(profile.cleaning_notes)
    consistency_component = max(0.0, 100 - coerced * 15)  # each needed-coercion column costs 15pts
    if coerced:
        notes.append(f"{coerced} column(s) needed type coercion during cleaning.")

    imbalance_component = 100.0
    if profile.class_imbalance and profile.class_imbalance.get("is_imbalanced"):
        majority = profile.class_imbalance["majority_class_pct"]
        imbalance_component = max(0.0, 100 - (majority - 50) * 2)  # 100%/0% split -> 0
        notes.append(f"Target candidate '{profile.class_imbalance['column']}' is imbalanced "
                     f"({majority:.1f}% majority class).")

    components = {
        "missing_values": round(missing_component, 1),
        "duplicate_rows": round(dup_component, 1),
        "outliers": round(outlier_component, 1),
        "feature_completeness": round(completeness_component, 1),
        "null_percentage": round(null_component, 1),
        "datatype_consistency": round(consistency_component, 1),
        "class_imbalance": round(imbalance_component, 1),
    }
    total = sum(components[k] * _QUALITY_WEIGHTS[k] for k in _QUALITY_WEIGHTS)
    return ScoreBreakdown(score=round(total, 1), label=_label(total), components=components, notes=notes)


def compute_readiness_score(
    profile: DatasetProfile, domain_result: DomainDetectionResult, quality: ScoreBreakdown,
    real_schema_match_pct: float | None = None,
) -> ScoreBreakdown:
    notes: list[str] = []

    avg_missing_pct = (
        sum(c.missing_pct for c in profile.columns) / len(profile.columns)
        if profile.columns else 0.0
    )
    missing_component = max(0.0, 100 - avg_missing_pct * 2.5)

    domain_component = domain_result.top_confidence

    if real_schema_match_pct is not None:
        # Real score from Segment 3's model selection — the best available
        # signal, since it's matched against an actual candidate model's
        # declared schema rather than the domain's generic feature list.
        schema_component = real_schema_match_pct
    else:
        # Proxy fallback for callers that haven't run model selection yet
        # (or when no models are registered for the detected domain at
        # all) — reuse the top domain's generic schema_completeness
        # sub-score as a rough stand-in.
        schema_component = 50.0
        if domain_result.all_scores and domain_result.all_scores[0].component_scores:
            schema_component = domain_result.all_scores[0].component_scores.get(
                "schema_completeness", 50.0
            )

    if profile.n_rows < 50:
        training_component = 20.0
        notes.append(f"Only {profile.n_rows} rows — most models need at least 50-100 for "
                     f"even minimally stable training.")
    elif profile.n_rows < 200:
        training_component = 60.0
        notes.append(f"{profile.n_rows} rows is workable but small; predictions may be less stable.")
    else:
        training_component = 100.0

    target_component = 100.0 if profile.target_column_candidates else 0.0
    if not profile.target_column_candidates:
        notes.append("No clear target column detected — predictions aren't possible without one; "
                     "exploratory insights are still available.")

    components = {
        "feature_quality": quality.score,
        "missing_values": round(missing_component, 1),
        "domain_confidence": round(domain_component, 1),
        "schema_match": round(schema_component, 1),
        "training_compatibility": round(training_component, 1),
        "target_availability": round(target_component, 1),
    }
    total = sum(components[k] * _READINESS_WEIGHTS[k] for k in _READINESS_WEIGHTS)
    return ScoreBreakdown(score=round(total, 1), label=_label(total), components=components, notes=notes)
