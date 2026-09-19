"""
Dataset Intelligence Engine — Step 14: Visualization Dashboard.

Produces chart *data/config*, not raster images — this stays a pure API;
Segment 9's frontend does the actual rendering. Every chart uses one
generic envelope so the frontend needs exactly one rendering component per
`type`, not one per chart:

    {"chart_id": str, "type": "bar"|"histogram"|"scatter"|"heatmap"|
     "pie"|"line"|"box", "title": str, "data": {...}}

`data` shapes are kept consistent within a type (arrays of {label, value}
or {x, y} objects) so `recharts` primitives can consume them with minimal
per-chart glue code — see IMPLEMENTATION_PLAN.md Segment 9 for the
frontend side of this contract.

Charts are computed on demand from already-stored profile/prediction data,
never persisted — rebuilding them is cheap and storing every chart's data
would bloat the DB for no benefit (this mirrors the decision documented in
the implementation plan).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.logging_config import get_logger
from services.dataset_intelligence.profiler import DatasetProfile

_log = get_logger(__name__)

MAX_CHARTS = 15
MAX_MISSING_MATRIX_COLS = 30
MAX_MISSING_MATRIX_ROWS = 200
MAX_DISTRIBUTION_CHARTS = 6  # cap per-column histograms/bars on wide datasets


@dataclass
class Chart:
    chart_id: str
    type: str  # "bar" | "histogram" | "scatter" | "heatmap" | "pie" | "line" | "box"
    title: str
    data: dict = field(default_factory=dict)


def _to_native(value):
    """Numpy scalar -> plain Python type. This bug class has hit every
    prior segment that touches numpy/pandas aggregations — cast
    defensively at every construction point in this file rather than
    hoping it doesn't recur."""
    if isinstance(value, np.generic):
        return value.item()
    return value


# ---------------------------------------------------------------------------
# Missing value matrix
# ---------------------------------------------------------------------------
def build_missing_value_matrix(df: pd.DataFrame, profile: DatasetProfile) -> Chart | None:
    if df.empty:
        return None
    cols = list(df.columns)[:MAX_MISSING_MATRIX_COLS]
    truncated_cols = len(df.columns) > MAX_MISSING_MATRIX_COLS

    if len(df) > MAX_MISSING_MATRIX_ROWS:
        sample = df[cols].sample(n=MAX_MISSING_MATRIX_ROWS, random_state=42).sort_index()
        sampled = True
    else:
        sample = df[cols]
        sampled = False

    grid = sample.isna().to_numpy().tolist()  # list[list[bool]], native Python bools already
    return Chart(
        chart_id="missing_value_matrix", type="heatmap", title="Missing Value Matrix",
        data={
            "columns": cols, "row_count": len(sample), "grid": grid,
            "truncated_columns": truncated_cols, "sampled_rows": sampled,
        },
    )


# ---------------------------------------------------------------------------
# Correlation heatmap — reuses Segment 2's already-computed matrix, no
# recomputation needed
# ---------------------------------------------------------------------------
def build_correlation_heatmap(profile: DatasetProfile) -> Chart | None:
    if not profile.correlation_matrix:
        return None
    cols = list(profile.correlation_matrix.keys())
    cells = []
    for a in cols:
        for b in cols:
            v = profile.correlation_matrix[a].get(b)
            cells.append({"x": a, "y": b, "value": v})
    return Chart(
        chart_id="correlation_heatmap", type="heatmap", title="Feature Correlation Matrix",
        data={"columns": cols, "cells": cells},
    )


# ---------------------------------------------------------------------------
# Per-column distributions — histograms for numerical, bar charts for
# categorical (reusing ColumnProfile.top_values, already computed)
# ---------------------------------------------------------------------------
def build_distribution_charts(df: pd.DataFrame, profile: DatasetProfile) -> list[Chart]:
    charts: list[Chart] = []

    for col in profile.numerical_columns[:MAX_DISTRIBUTION_CHARTS]:
        col_profile = next((c for c in profile.columns if c.name == col), None)
        if col_profile and profile.n_rows > 0 and _looks_like_id_column(col, col_profile, profile.n_rows):
            continue  # e.g. "patient_id" — a histogram of an identifier is meaningless clutter
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if series.empty:
            continue
        counts, bin_edges = np.histogram(series, bins=min(20, max(5, series.nunique())))
        buckets = [
            {"label": f"{bin_edges[i]:.1f}\u2013{bin_edges[i+1]:.1f}", "value": _to_native(counts[i])}
            for i in range(len(counts))
        ]
        charts.append(Chart(
            chart_id=f"histogram_{col}", type="histogram", title=f"Distribution of {col}",
            data={"buckets": buckets},
        ))

    for col_name in profile.categorical_columns[:MAX_DISTRIBUTION_CHARTS]:
        col_profile = next((c for c in profile.columns if c.name == col_name), None)
        if col_profile is None or not col_profile.top_values:
            continue
        bars = [{"label": k, "value": v} for k, v in col_profile.top_values.items()]
        charts.append(Chart(
            chart_id=f"bar_{col_name}", type="bar", title=f"Top values \u2014 {col_name}",
            data={"bars": bars},
        ))

    return charts


def _looks_like_id_column(name: str, col_profile, n_rows: int) -> bool:
    """Deliberately requires BOTH signals together, not either alone:

    - High cardinality alone is unreliable at small sample sizes — a
      genuine continuous measurement (age, blood_pressure) will often be
      close to 100% unique purely because real-valued data rarely repeats
      exactly in a small sample. Filtering on cardinality alone caused a
      real regression during testing: it silently dropped histograms for
      every legitimate numerical column in a 20-row test dataset, not just
      the actual ID column.
    - Name pattern alone ("ends with id") isn't sufficient either — it's
      the combination with high cardinality that distinguishes an
      identifier from a legitimately-named field that happens to end in
      "id" (rare, but possible).
    """
    normalized = name.strip().lower().replace("-", "_")
    name_suggests_id = normalized == "id" or normalized.endswith("_id") or normalized.endswith("id")
    high_cardinality = n_rows > 0 and col_profile.unique_count / n_rows >= 0.95
    return name_suggests_id and high_cardinality


# ---------------------------------------------------------------------------
# Outlier box-plot summary stats per numerical column
# ---------------------------------------------------------------------------
def build_outlier_chart(df: pd.DataFrame, profile: DatasetProfile) -> Chart | None:
    if not profile.numerical_columns:
        return None
    boxes = []
    for col in profile.numerical_columns:
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if series.empty:
            continue
        desc = series.describe()
        boxes.append({
            "column": col, "min": _to_native(desc["min"]), "q1": _to_native(series.quantile(0.25)),
            "median": _to_native(desc["50%"]), "q3": _to_native(series.quantile(0.75)),
            "max": _to_native(desc["max"]),
        })
    if not boxes:
        return None
    return Chart(chart_id="outlier_boxplot", type="box", title="Outlier Summary (Box Plot Stats)",
                 data={"boxes": boxes})


# ---------------------------------------------------------------------------
# Feature importance (from SHAP global importance, Segment 4 output).
# Takes a plain dict rather than the full ExplainabilityResult so it works
# equally from a fresh pipeline run (in-memory dataclass) or from the API
# route re-reading DatasetPredictionRecord.global_importance_json out of
# storage — those are the two real call sites, and only one of them ever
# has a live ExplainabilityResult object.
# ---------------------------------------------------------------------------
def build_feature_importance_chart(global_importance: dict | None) -> Chart | None:
    if not global_importance:
        return None
    bars = [{"label": k, "value": float(v)} for k, v in global_importance.items()]
    return Chart(chart_id="feature_importance", type="bar", title="Feature Importance (SHAP)",
                 data={"bars": bars})


# ---------------------------------------------------------------------------
# Prediction distribution (Segment 4 output). Same plain-primitive
# rationale as build_feature_importance_chart above.
# ---------------------------------------------------------------------------
def build_prediction_distribution_chart(
    problem_type: str | None, target_variable: str | None, prediction_distribution: dict | None,
) -> Chart | None:
    if not prediction_distribution or not problem_type:
        return None
    if problem_type == "regression":
        return Chart(
            chart_id="prediction_distribution", type="bar", title="Prediction Summary",
            data={"bars": [{"label": k, "value": float(v)} for k, v in prediction_distribution.items()]},
        )
    return Chart(
        chart_id="prediction_distribution", type="pie", title=f"Predicted {target_variable}",
        data={"slices": [{"label": k, "value": int(v)} for k, v in prediction_distribution.items()]},
    )


# ---------------------------------------------------------------------------
# Confusion matrix + ROC — ONLY when the uploaded dataset genuinely
# contains the model's target_variable column (a labeled validation set,
# not just inference input). Returns [] otherwise rather than fabricating
# metrics against data the model was never validated on.
# ---------------------------------------------------------------------------
def build_confusion_matrix_and_roc(
    df: pd.DataFrame, target_variable: str | None, problem_type: str | None,
    row_predictions: list[dict],
) -> list[Chart]:
    """NOTE on scale: this only sees `row_predictions`, which prediction.py
    caps at MAX_ROW_PREDICTIONS_RETURNED (200) even when more rows were
    actually predicted. For datasets under that cap (true for every
    dataset tested so far) this covers 100% of predictions. For larger
    datasets, the confusion matrix/ROC would be computed on a 200-row
    sample rather than the full set — acceptable for a dashboard preview,
    but worth flagging if this chart is ever used for a compliance-grade
    evaluation report rather than exploratory viewing.

    Each entry in `row_predictions` is a plain dict with keys
    `row_index, prediction, probability, class_probabilities` — matching
    both RowPrediction.__dict__ (fresh pipeline run) and
    DatasetPredictionRecord.row_predictions_json (API route re-read from
    storage), so this works from either call site without an adapter.
    """
    if not row_predictions or problem_type != "binary_classification" or not target_variable:
        return []

    import re
    def normalize(s: str) -> str:
        return re.sub(r"[_\-]+", " ", s.strip().lower())

    target_norm = normalize(target_variable)
    label_col = None
    for col in df.columns:
        if normalize(col) == target_norm:
            label_col = col
            break
    if label_col is None:
        return []  # not a labeled validation set — nothing to score against

    try:
        from sklearn.metrics import confusion_matrix, roc_curve, auc

        y_true_full = pd.to_numeric(df[label_col], errors="coerce")
        predicted_indices = [r["row_index"] for r in row_predictions]
        y_true = y_true_full.loc[predicted_indices].to_numpy()
        y_pred = np.array([r["prediction"] for r in row_predictions])
        valid = ~np.isnan(y_true)
        y_true, y_pred = y_true[valid], y_pred[valid]

        if len(y_true) < 5 or len(np.unique(y_true)) < 2:
            return []  # not enough labeled, balanced data to compute meaningful metrics

        cm = confusion_matrix(y_true, y_pred)
        cm_chart = Chart(
            chart_id="confusion_matrix", type="heatmap", title="Confusion Matrix (vs. ground truth in upload)",
            data={
                "columns": ["Predicted 0", "Predicted 1"],
                "cells": [
                    {"x": "Predicted 0", "y": "Actual 0", "value": int(cm[0][0])},
                    {"x": "Predicted 1", "y": "Actual 0", "value": int(cm[0][1])},
                    {"x": "Predicted 0", "y": "Actual 1", "value": int(cm[1][0])},
                    {"x": "Predicted 1", "y": "Actual 1", "value": int(cm[1][1])},
                ],
            },
        )

        y_scores = np.array([
            (r.get("class_probabilities") or {}).get("1", r.get("probability") or 0.0)
            for r in row_predictions
        ])[valid]
        fpr, tpr, _ = roc_curve(y_true, y_scores)
        roc_auc = auc(fpr, tpr)
        roc_chart = Chart(
            chart_id="roc_curve", type="line", title=f"ROC Curve (AUC={roc_auc:.3f})",
            data={"points": [{"x": float(f), "y": float(t)} for f, t in zip(fpr, tpr)], "auc": float(roc_auc)},
        )
        return [cm_chart, roc_chart]
    except Exception as exc:  # noqa: BLE001
        _log.warning("visualization.confusion_matrix_failed", error=str(exc))
        return []  # optional bonus chart — never fail the whole chart set over this


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def build_all_charts(
    df: pd.DataFrame, profile: DatasetProfile,
    global_importance: dict | None = None,
    problem_type: str | None = None, target_variable: str | None = None,
    prediction_distribution: dict | None = None, row_predictions: list[dict] | None = None,
) -> list[Chart]:
    charts: list[Chart] = []

    missing = build_missing_value_matrix(df, profile)
    if missing:
        charts.append(missing)

    corr = build_correlation_heatmap(profile)
    if corr:
        charts.append(corr)

    charts.extend(build_distribution_charts(df, profile))

    outliers = build_outlier_chart(df, profile)
    if outliers:
        charts.append(outliers)

    importance = build_feature_importance_chart(global_importance)
    if importance:
        charts.append(importance)

    pred_dist = build_prediction_distribution_chart(problem_type, target_variable, prediction_distribution)
    if pred_dist:
        charts.append(pred_dist)

    charts.extend(build_confusion_matrix_and_roc(df, target_variable, problem_type, row_predictions or []))

    return charts[:MAX_CHARTS]
