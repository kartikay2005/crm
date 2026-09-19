"""
Dataset Intelligence Engine — Step 7: Schema Matching.

Scores how well a dataset's profiled columns satisfy a candidate model's
declared schema. Weighted so that missing REQUIRED features hurt far more
than missing optional ones, dtype mismatches, or excess missingness — a
model with 3/4 required features present is a much weaker candidate than
one description alone would suggest.

  - required_coverage (60%) — fraction of required_features present
                               (fuzzy-matched against profiled column names)
  - dtype_compatibility (20%) — of the required features that ARE present,
                               fraction whose profiled type matches the
                               model's declared dtype
  - missing_data_ok (10%)    — whether missingness on matched columns stays
                               within the model's allowed_missing_pct
  - optional_bonus (10%)     — fraction of optional_features also present
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.dataset_intelligence.model_registry import FeatureSpec, ModelMetadata
from services.dataset_intelligence.profiler import DatasetProfile
from services.dataset_intelligence.text_matching import matches, normalize

_WEIGHTS = {
    "required_coverage": 0.60,
    "dtype_compatibility": 0.20,
    "missing_data_ok": 0.10,
    "optional_bonus": 0.10,
}


@dataclass
class SchemaMatchResult:
    model_name: str
    match_pct: float
    matched_required: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    matched_optional: list[str] = field(default_factory=list)
    dtype_mismatches: list[str] = field(default_factory=list)
    missing_data_violations: list[str] = field(default_factory=list)


def _find_column(feature_name: str, profile: DatasetProfile):
    """One-directional, word-boundary matching — see text_matching.py's
    module docstring for the two bugs this specifically prevents (reverse
    containment matching a short real column name inside a longer
    canonical term, and short canonical terms coincidentally appearing as
    a raw substring of an unrelated word, e.g. 'age' inside 'average')."""
    target = normalize(feature_name)
    for col in profile.columns:
        if normalize(col.name) == target or matches(feature_name, col.name):
            return col
    return None


def _dtype_compatible(feat: FeatureSpec, col) -> bool:
    if feat.dtype == col.inferred_type:
        return True
    # Booleans and low-cardinality numerics are frequently interchangeable
    # in practice (e.g. a 0/1 numeric column IS a boolean flag) — treat as
    # compatible rather than penalizing a cosmetic type difference.
    if {feat.dtype, col.inferred_type} == {"boolean", "numerical"}:
        return True
    return False


def match_schema(profile: DatasetProfile, model: ModelMetadata) -> SchemaMatchResult:
    result = SchemaMatchResult(model_name=model.name, match_pct=0.0)

    matched_required_cols = []
    for feat in model.required_features:
        col = _find_column(feat.name, profile)
        if col is None:
            result.missing_required.append(feat.name)
            continue
        result.matched_required.append(feat.name)
        matched_required_cols.append((feat, col))

    for feat in model.optional_features:
        if _find_column(feat.name, profile) is not None:
            result.matched_optional.append(feat.name)

    n_required = len(model.required_features) or 1
    required_coverage = 100 * len(result.matched_required) / n_required

    dtype_ok = 0
    for feat, col in matched_required_cols:
        if _dtype_compatible(feat, col):
            dtype_ok += 1
        else:
            result.dtype_mismatches.append(
                f"{feat.name}: expected {feat.dtype}, dataset has {col.inferred_type}"
            )
    dtype_compatibility = (
        100 * dtype_ok / len(matched_required_cols) if matched_required_cols else 0.0
    )

    missing_ok = 0
    for feat, col in matched_required_cols:
        if col.missing_pct <= model.allowed_missing_pct:
            missing_ok += 1
        else:
            result.missing_data_violations.append(
                f"{feat.name}: {col.missing_pct:.1f}% missing, model allows "
                f"{model.allowed_missing_pct:.1f}%"
            )
    missing_data_ok = (
        100 * missing_ok / len(matched_required_cols) if matched_required_cols else 0.0
    )

    n_optional = len(model.optional_features) or 1
    optional_bonus = 100 * len(result.matched_optional) / n_optional if model.optional_features else 100.0

    total = (
        required_coverage * _WEIGHTS["required_coverage"]
        + dtype_compatibility * _WEIGHTS["dtype_compatibility"]
        + missing_data_ok * _WEIGHTS["missing_data_ok"]
        + optional_bonus * _WEIGHTS["optional_bonus"]
    )
    result.match_pct = round(total, 1)
    return result
