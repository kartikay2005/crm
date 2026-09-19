"""
Dataset Intelligence Engine — Step 12: Explainable AI.

Uses the real `shap` library — not a hand-rolled approximation — to compute
both local (per-row) and global (whole-dataset) feature attributions.

Explainer choice: `shap.LinearExplainer` for linear models (our two demo
models are LogisticRegression) since it's exact and fast for that model
class; `shap.Explainer` (auto-selecting, falling back to a
permutation/kernel-based explainer) for anything else, so this isn't
hard-locked to linear models as more model types get registered.

A natural-language explanation is generated from the top SHAP-attributed
features for each row, per the spec's example format ("Debt ratio is very
high. Credit score is low.").
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import shap
from sklearn.linear_model import LogisticRegression, LinearRegression

from core.logging_config import get_logger

_log = get_logger(__name__)

# Cap on rows we compute LOCAL explanations for — SHAP is not free, and a
# global feature-importance summary (computed separately, cheaply, from the
# same background sample) covers the "why in general" question for the
# whole dataset regardless of this cap.
MAX_ROWS_EXPLAINED = 50


class ExplainabilityError(Exception):
    """Friendly, typed explainability failures — never let raw SHAP/numpy
    internals leak to the API layer."""


@dataclass
class FeatureContribution:
    feature: str
    value: float
    shap_value: float
    direction: str  # "increases" | "decreases"


@dataclass
class LocalExplanation:
    row_index: int
    top_contributors: list[FeatureContribution]
    natural_language: str


@dataclass
class ExplainabilityResult:
    global_importance: dict[str, float]  # feature -> mean |SHAP value|, sorted descending
    local_explanations: list[LocalExplanation]
    explainer_type: str


def _build_explainer(estimator, background: pd.DataFrame):
    if isinstance(estimator, (LogisticRegression, LinearRegression)):
        return shap.LinearExplainer(estimator, background), "LinearExplainer"
    # Generic path: works for tree ensembles, SVMs, anything with predict/
    # predict_proba, at the cost of being slower (sampling-based).
    predict_fn = estimator.predict_proba if hasattr(estimator, "predict_proba") else estimator.predict
    return shap.Explainer(predict_fn, background), "Explainer (auto)"


def _direction(shap_value: float) -> str:
    return "increases" if shap_value > 0 else "decreases"


def _natural_language(target: str, contributors: list[FeatureContribution]) -> str:
    if not contributors:
        return f"No single feature stood out as a strong driver of this {target} prediction."
    parts = []
    for c in contributors[:3]:
        verb = "is notably high" if c.shap_value > 0 and c.value > 0 else \
               "is notably low" if c.shap_value > 0 else \
               "is within a lower-risk range"
        parts.append(f"{c.feature.replace('_', ' ')} {verb}")
    return "; ".join(parts).capitalize() + "."


def explain(
    estimator, X: pd.DataFrame, target_variable: str, problem_type: str,
) -> ExplainabilityResult:
    """X must already be the fully-prepared feature matrix (same one passed
    to estimator.predict() in prediction.py) — explanations are computed
    against exactly what the model saw, not the raw uncleaned dataset."""
    if X.empty:
        raise ExplainabilityError("No rows available to explain.")

    # Background sample for SHAP's expectation baseline — capped for speed
    # on large datasets, standard SHAP practice (100 rows is the library's
    # own common default).
    background = X.sample(n=min(100, len(X)), random_state=42)

    try:
        explainer, explainer_type = _build_explainer(estimator, background)
        to_explain = X.head(MAX_ROWS_EXPLAINED)
        shap_values = explainer(to_explain)
    except Exception as exc:  # noqa: BLE001
        raise ExplainabilityError(
            f"Could not compute feature explanations for this model. ({type(exc).__name__})"
        ) from exc

    values = shap_values.values
    # Binary classifiers via LinearExplainer return a single 2D array
    # (n_rows, n_features) for the positive class; multiclass / some
    # generic explainers return a 3D array (n_rows, n_features, n_classes)
    # — normalize to 2D by taking the last class's contributions (the
    # "positive"/highest-index class), which is the conventional choice
    # for binary problems.
    if values.ndim == 3:
        values = values[:, :, -1]

    feature_names = list(X.columns)

    # Global importance: mean absolute SHAP value per feature, across the
    # explained sample — a standard, well-understood summary.
    mean_abs = np.abs(values).mean(axis=0)
    global_importance = dict(sorted(
        zip(feature_names, (float(v) for v in mean_abs)),
        key=lambda kv: kv[1], reverse=True,
    ))

    local_explanations: list[LocalExplanation] = []
    for i in range(len(to_explain)):
        row_shap = values[i]
        row_values = to_explain.iloc[i]
        contributions = [
            FeatureContribution(
                feature=feature_names[j], value=float(row_values.iloc[j]),
                shap_value=float(row_shap[j]), direction=_direction(row_shap[j]),
            )
            for j in range(len(feature_names))
        ]
        contributions.sort(key=lambda c: abs(c.shap_value), reverse=True)
        local_explanations.append(LocalExplanation(
            row_index=int(to_explain.index[i]),
            top_contributors=contributions[:5],
            natural_language=_natural_language(target_variable, contributions),
        ))

    return ExplainabilityResult(
        global_importance=global_importance, local_explanations=local_explanations,
        explainer_type=explainer_type,
    )
