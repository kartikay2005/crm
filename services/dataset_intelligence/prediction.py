"""
Dataset Intelligence Engine — Step 11: Prediction Engine.

Loads the actual `.joblib` artifact for a selected model (see Segment 3's
model_registry/model_selection), builds a feature matrix from the raw
dataframe matching the model's declared schema, and runs real inference —
not simulated output.

Scope for this segment: any scikit-learn-compatible estimator exposing
`predict()` (and `predict_proba()` for classifiers) works generically,
since both our real demo models (Segment 3's training script) are
LogisticRegression and expose that standard interface. This covers
binary_classification, multiclass_classification, and regression cleanly.

forecasting, clustering, and anomaly_detection need fundamentally
different pipelines — time-series windowing, distance-threshold logic, no
ground-truth labels to score against — and are deliberately out of scope
here rather than faked with a generic .predict() call that would silently
produce meaningless output. `predict_dataset` raises a clear, typed error
for those problem types rather than pretending to support them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd

from core.logging_config import get_logger
from services.dataset_intelligence.model_registry import ModelMetadata
from services.dataset_intelligence.text_matching import matches, normalize

_log = get_logger(__name__)

SUPPORTED_PROBLEM_TYPES = {"binary_classification", "multiclass_classification", "regression"}

# Cap on how many per-row predictions we return in an API response — large
# datasets could otherwise produce a multi-MB payload. Summary stats always
# cover the full dataset regardless of this cap.
MAX_ROW_PREDICTIONS_RETURNED = 200


class PredictionError(Exception):
    """Friendly, typed prediction failures. Never let a raw sklearn/numpy
    exception reach the API layer."""


@dataclass
class RowPrediction:
    row_index: int
    prediction: float | int | str
    probability: float | None  # confidence in the predicted class, classification only
    class_probabilities: dict[str, float] | None = None  # full distribution, classification only


@dataclass
class PredictionResult:
    model_name: str
    model_version: str
    problem_type: str
    target_variable: str
    n_rows_predicted: int
    row_predictions: list[RowPrediction] = field(default_factory=list)  # capped, see MAX_ROW_PREDICTIONS_RETURNED
    prediction_distribution: dict[str, int | float] = field(default_factory=dict)
    inference_time_seconds: float = 0.0
    rows_per_second: float = 0.0
    feature_matrix_columns: list[str] = field(default_factory=list)
    rows_dropped_missing_features: int = 0


def _to_native(value):
    """Numpy scalar -> plain Python type, for JSON safety (see Segment 1/2
    postmortems on this exact class of bug)."""
    if isinstance(value, np.generic):
        return value.item()
    return value


def _load_artifact(model: ModelMetadata):
    if not model.artifact_available:
        raise PredictionError(
            f"'{model.name}' has no trained artifact available — this should have "
            f"been caught by model selection's fallback logic before reaching here."
        )
    try:
        return joblib.load(model.artifact_path)
    except Exception as exc:  # noqa: BLE001
        raise PredictionError(
            f"Could not load the trained model file for '{model.name}'. "
            f"It may be corrupted or built with an incompatible library version."
        ) from exc


def _build_feature_matrix(df: pd.DataFrame, model: ModelMetadata) -> tuple[pd.DataFrame, int]:
    """Selects and orders columns to match the model's required_features
    (word-boundary matched against actual dataset column names via
    text_matching.py, shared with schema_matching.py so the two stay
    consistent), imputes missing values with the column median
    (numerical) or mode (categorical) rather than dropping rows outright —
    dropping rows would silently shrink the prediction set with no visible
    signal to the caller.

    Rows where a required feature is entirely unparseable (not just
    missing, but genuinely non-numeric where a number is required) ARE
    dropped, and the count is returned so the caller can be honest about it
    in the response rather than pretending every row got a prediction.
    """
    col_lookup = {normalize(c): c for c in df.columns}

    feature_cols: list[str] = []
    for feat in model.required_features:
        target = normalize(feat.name)
        match = None
        for norm, original in col_lookup.items():
            if norm == target or matches(feat.name, original):
                match = original
                break
        if match is None:
            raise PredictionError(
                f"Required feature '{feat.name}' not found in the dataset — "
                f"this should have been caught by schema matching before reaching here."
            )
        feature_cols.append(match)

    X = df[feature_cols].copy()
    X.columns = [feat.name for feat in model.required_features]  # normalize to model's expected names

    rows_before = len(X)
    for col in X.columns:
        coerced = pd.to_numeric(X[col], errors="coerce")
        if coerced.notna().mean() >= 0.5:
            median = coerced.median()
            X[col] = coerced.fillna(median)
        else:
            mode = X[col].mode(dropna=True)
            X[col] = X[col].fillna(mode.iloc[0] if not mode.empty else 0)

    # Drop rows that still have any NaN after imputation (only possible if
    # an entire column was unparseable, e.g. all-text where numeric expected)
    X = X.dropna()
    rows_dropped = rows_before - len(X)

    return X, rows_dropped


def predict_dataset(df: pd.DataFrame, model: ModelMetadata) -> PredictionResult:
    if model.problem_type not in SUPPORTED_PROBLEM_TYPES:
        raise PredictionError(
            f"'{model.problem_type}' prediction is not yet supported by this pipeline "
            f"segment — forecasting, clustering, and anomaly detection need dedicated "
            f"pipelines beyond generic .predict(). This is a scope limitation, not a "
            f"data problem."
        )

    estimator = _load_artifact(model)
    X, rows_dropped = _build_feature_matrix(df, model)

    if X.empty:
        raise PredictionError(
            "No rows had usable values for all required features after cleaning — "
            "no predictions could be made."
        )

    start = time.perf_counter()
    n_features_expected = getattr(estimator, "n_features_in_", None)
    if n_features_expected is not None and n_features_expected != X.shape[1]:
        raise PredictionError(
            f"'{model.name}' was trained on {n_features_expected} feature(s), but its "
            f"registry metadata declares {X.shape[1]} required_features. This is a "
            f"registry configuration problem (metadata doesn't match what the model "
            f"was actually fitted on) — required_features must exactly match the "
            f"model's training feature set, not just the domain's schema-matching "
            f"signature."
        )
    try:
        raw_predictions = estimator.predict(X)
        probabilities = None
        class_labels = None
        if hasattr(estimator, "predict_proba"):
            probabilities = estimator.predict_proba(X)
            class_labels = [str(c) for c in getattr(estimator, "classes_", range(probabilities.shape[1]))]
    except Exception as exc:  # noqa: BLE001
        raise PredictionError(
            f"The model failed to run on this data. This usually means the "
            f"feature values are out of the range the model was trained on. "
            f"({type(exc).__name__})"
        ) from exc
    elapsed = time.perf_counter() - start

    row_predictions: list[RowPrediction] = []
    for i in range(min(len(X), MAX_ROW_PREDICTIONS_RETURNED)):
        pred_value = raw_predictions[i]
        prob = None
        class_probs = None
        if probabilities is not None:
            class_probs = {class_labels[j]: float(probabilities[i, j]) for j in range(probabilities.shape[1])}
            prob = float(max(probabilities[i]))
        row_predictions.append(RowPrediction(
            row_index=int(X.index[i]),
            prediction=_to_native(pred_value),
            probability=prob,
            class_probabilities=class_probs,
        ))

    if model.problem_type == "regression":
        distribution = {
            "mean": float(np.mean(raw_predictions)), "min": float(np.min(raw_predictions)),
            "max": float(np.max(raw_predictions)), "std": float(np.std(raw_predictions)),
        }
    else:
        unique, counts = np.unique(raw_predictions, return_counts=True)
        distribution = {str(_to_native(u)): int(c) for u, c in zip(unique, counts)}

    return PredictionResult(
        model_name=model.name, model_version=model.version, problem_type=model.problem_type,
        target_variable=model.target_variable, n_rows_predicted=len(X),
        row_predictions=row_predictions, prediction_distribution=distribution,
        inference_time_seconds=round(elapsed, 4),
        rows_per_second=round(len(X) / elapsed, 1) if elapsed > 0 else float(len(X)),
        feature_matrix_columns=list(X.columns), rows_dropped_missing_features=rows_dropped,
    )
