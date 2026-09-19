"""
Dataset Intelligence Engine — Step 16: Recommendation Engine.

Domain-aware, rule-based, additive: each domain gets its own list of rule
functions, and adding a new domain's recommendations is "append a function
to a list," never a change to the dispatch logic. A domain with no rules
registered yet returns an empty list — same fallback-friendly pattern used
everywhere else in this pipeline, not an error.

Every rule function has the same signature so the registry stays uniform:
    (profile, prediction, quality) -> Recommendation | None
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from services.dataset_intelligence.prediction import PredictionResult
from services.dataset_intelligence.profiler import DatasetProfile
from services.dataset_intelligence.quality_readiness import ScoreBreakdown
from services.dataset_intelligence.text_matching import find_column


@dataclass
class Recommendation:
    text: str
    priority: str  # "high" | "medium" | "low"
    domain: str
    rationale: str


RuleFn = Callable[[DatasetProfile, "PredictionResult | None", ScoreBreakdown], "Recommendation | None"]


def _find_column(profile: DatasetProfile, *candidates: str):
    """Word-boundary matches a column against one or more candidate terms,
    returning the matched ColumnProfile (or None).

    This function is where BOTH bugs in this family were originally
    caught during testing:
    - Reverse substring containment (candidate found within column name OR
      vice versa) let a short real column name ("income") falsely match
      inside an unrelated longer candidate phrase ("debt to income"),
      producing a Finance recommendation with the nonsensical rationale
      "average debt ratio is 51266.67" — that was actually the income
      column's mean, not debt ratio at all.
    - Even after restricting to one direction, raw substring containment
      still let short candidate terms coincidentally match inside
      unrelated words (e.g. "age" inside "average").
    Both are now handled by services.dataset_intelligence.text_matching's
    word-boundary matcher, shared with domain_detection.py,
    schema_matching.py, and prediction.py so this bug family can't
    reappear independently in any of the four places it used to be
    duplicated.
    """
    matched_name = find_column(list(candidates), [c.name for c in profile.columns])
    if matched_name is None:
        return None
    return next(c for c in profile.columns if c.name == matched_name)


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
def _finance_debt_ratio_rule(profile, prediction, quality) -> Recommendation | None:
    col = _find_column(profile, "debt ratio", "debt to income")
    if col is None or col.mean is None:
        return None
    if col.mean > 0.6:
        return Recommendation(
            text="Reduce lending to customers with Debt Ratio > 0.7.",
            priority="high", domain="Finance",
            rationale=f"Average debt ratio in this dataset is {col.mean:.2f}, well above "
                      f"a healthy threshold.",
        )
    return None


def _finance_prediction_risk_rule(profile, prediction, quality) -> Recommendation | None:
    if prediction is None or prediction.problem_type not in ("binary_classification",):
        return None
    dist = prediction.prediction_distribution
    total = sum(dist.values()) or 1
    high_risk_pct = 100 * dist.get("1", 0) / total
    if high_risk_pct > 30:
        return Recommendation(
            text="Tighten approval criteria — a large share of applicants are predicted high-risk.",
            priority="high", domain="Finance",
            rationale=f"{high_risk_pct:.0f}% of scored rows were predicted high-risk for "
                      f"{prediction.target_variable}.",
        )
    return None


# ---------------------------------------------------------------------------
# Healthcare
# ---------------------------------------------------------------------------
def _healthcare_bmi_rule(profile, prediction, quality) -> Recommendation | None:
    col = _find_column(profile, "bmi")
    if col is None or col.mean is None:
        return None
    if col.mean > 27:
        return Recommendation(
            text="Patients with BMI > 30 should be flagged for monitoring.",
            priority="medium", domain="Healthcare",
            rationale=f"Average BMI in this dataset is {col.mean:.1f}, above the healthy range.",
        )
    return None


def _healthcare_prediction_followup_rule(profile, prediction, quality) -> Recommendation | None:
    if prediction is None or prediction.problem_type != "binary_classification":
        return None
    dist = prediction.prediction_distribution
    total = sum(dist.values()) or 1
    elevated_pct = 100 * dist.get("1", 0) / total
    if elevated_pct > 25:
        return Recommendation(
            text="Schedule proactive follow-up outreach for patients flagged elevated-risk.",
            priority="high", domain="Healthcare",
            rationale=f"{elevated_pct:.0f}% of patients were predicted elevated-risk for "
                      f"{prediction.target_variable}.",
        )
    return None


# ---------------------------------------------------------------------------
# Retail
# ---------------------------------------------------------------------------
def _retail_low_quantity_rule(profile, prediction, quality) -> Recommendation | None:
    col = _find_column(profile, "quantity", "stock", "inventory")
    if col is None or col.mean is None or col.min is None:
        return None
    # Heuristic only — no historical reorder-point data available at this
    # stage, so this flags "some rows are near zero" rather than computing
    # a real reorder point. Documented limitation, not a precise forecast.
    if col.min <= max(0.1 * col.mean, 1):
        return Recommendation(
            text="Restock low-inventory products within the next few days.",
            priority="medium", domain="Retail",
            rationale=f"At least one product's quantity ({col.min:.0f}) is far below the "
                      f"dataset average ({col.mean:.1f}).",
        )
    return None


# ---------------------------------------------------------------------------
# Marketing / HR — single generic starter rule each, extend as real
# datasets surface better signals (this is exactly the kind of addition
# the registry pattern is designed to make cheap).
# ---------------------------------------------------------------------------
def _marketing_low_conversion_rule(profile, prediction, quality) -> Recommendation | None:
    col = _find_column(profile, "conversion rate", "ctr")
    if col is None or col.mean is None:
        return None
    if col.mean < 0.02:
        return Recommendation(
            text="Review underperforming campaigns — conversion rate is low across the board.",
            priority="medium", domain="Marketing",
            rationale=f"Average conversion rate is {col.mean:.2%}.",
        )
    return None


def _hr_high_attrition_rule(profile, prediction, quality) -> Recommendation | None:
    if prediction is None or "attrition" not in prediction.target_variable.lower():
        return None
    dist = prediction.prediction_distribution
    total = sum(dist.values()) or 1
    attrition_pct = 100 * dist.get("1", 0) / total
    if attrition_pct > 20:
        return Recommendation(
            text="Investigate retention drivers for high-attrition-risk employee segments.",
            priority="high", domain="HR",
            rationale=f"{attrition_pct:.0f}% of employees were predicted high attrition risk.",
        )
    return None


_DOMAIN_RULES: dict[str, list[RuleFn]] = {
    "Finance": [_finance_debt_ratio_rule, _finance_prediction_risk_rule],
    "Healthcare": [_healthcare_bmi_rule, _healthcare_prediction_followup_rule],
    "Retail": [_retail_low_quantity_rule],
    "Marketing": [_marketing_low_conversion_rule],
    "HR": [_hr_high_attrition_rule],
}

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def generate_recommendations(
    domain: str, profile: DatasetProfile, prediction: PredictionResult | None, quality: ScoreBreakdown,
) -> list[Recommendation]:
    rules = _DOMAIN_RULES.get(domain, [])
    results = []
    for rule in rules:
        try:
            rec = rule(profile, prediction, quality)
        except Exception:  # noqa: BLE001
            # One bad rule shouldn't take down recommendations for every
            # other rule in the domain — same "one bad entry, not the whole
            # system" pattern as the model registry loader.
            continue
        if rec is not None:
            results.append(rec)
    results.sort(key=lambda r: _PRIORITY_ORDER.get(r.priority, 99))
    return results
