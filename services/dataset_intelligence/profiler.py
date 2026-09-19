"""
Dataset Intelligence Engine — Step 2: Dataset Profiling.

Produces a single JSON-serializable DatasetProfile covering every field the
spec calls for: shape, missingness, duplicates, per-column type breakdown,
target-column candidates, constant columns, outliers, distributions,
correlation matrix, skewness, kurtosis, and class-imbalance signal.

Performance note: correlation/skew/kurtosis are computed on a capped sample
(`dataset_profile_sample_rows`, default 200k) for very large files — see
core/config.py. Row/column counts, missingness, and dtype breakdowns always
use the full dataset since those are cheap vectorized operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd

from core.config import get_settings

_settings = get_settings()


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    inferred_type: str  # "numerical" | "categorical" | "datetime" | "boolean" | "text" | "constant"
    missing_count: int
    missing_pct: float
    unique_count: int
    is_constant: bool
    is_target_candidate: bool
    # numerical-only
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None
    outlier_pct: float | None = None
    # categorical-only
    top_values: dict[str, int] | None = None
    cardinality_ratio: float | None = None  # unique / total, flags likely-ID columns


@dataclass
class DatasetProfile:
    dataset_name: str
    n_rows: int
    n_columns: int
    memory_usage_bytes: int
    duplicate_row_count: int
    duplicate_row_pct: float
    columns: list[ColumnProfile] = field(default_factory=list)
    numerical_columns: list[str] = field(default_factory=list)
    categorical_columns: list[str] = field(default_factory=list)
    datetime_columns: list[str] = field(default_factory=list)
    boolean_columns: list[str] = field(default_factory=list)
    constant_columns: list[str] = field(default_factory=list)
    target_column_candidates: list[str] = field(default_factory=list)
    correlation_matrix: dict[str, dict[str, float]] | None = None
    class_imbalance: dict[str, Any] | None = None
    sampled_for_stats: bool = False
    sample_size: int | None = None
    cleaning_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _infer_column_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "numerical"
    non_null = series.dropna()
    if non_null.empty:
        return "categorical"
    # Heuristic: high-cardinality object columns with long average string
    # length look like free text rather than a categorical field.
    avg_len = non_null.astype(str).str.len().mean()
    cardinality_ratio = non_null.nunique() / max(len(non_null), 1)
    if avg_len > 30 and cardinality_ratio > 0.5:
        return "text"
    return "categorical"


def _outlier_pct_iqr(series: pd.Series) -> float:
    non_null = series.dropna()
    if len(non_null) < 4:
        return 0.0
    q1, q3 = non_null.quantile(0.25), non_null.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return 0.0
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = ((non_null < lower) | (non_null > upper)).sum()
    return float(round(100 * outliers / len(non_null), 2))


def _is_target_candidate(name: str, col_type: str, cardinality_ratio: float | None,
                          n_rows: int, unique_count: int) -> bool:
    """A column is a plausible prediction target if it's not an obvious ID
    (low cardinality relative to row count, or explicit target-like name)
    and isn't constant or all-unique."""
    if unique_count <= 1 or unique_count >= n_rows:
        return False
    name_lower = name.lower()
    target_keywords = (
        "target", "label", "outcome", "class", "churn", "default", "fraud",
        "diagnosis", "result", "status", "risk", "price", "revenue", "sales",
        "converted", "response", "y",
    )
    name_hints = any(k in name_lower for k in target_keywords)
    if col_type == "boolean":
        return True
    if col_type == "categorical" and cardinality_ratio is not None and cardinality_ratio < 0.2:
        return True
    if col_type == "numerical" and name_hints:
        return True
    return name_hints


def profile_dataset(df: pd.DataFrame, dataset_name: str, cleaning_notes: list[str] | None = None) -> DatasetProfile:
    n_rows, n_cols = df.shape
    sampled = False
    sample_size = None
    stats_df = df
    if n_rows > _settings.dataset_profile_sample_rows:
        stats_df = df.sample(n=_settings.dataset_profile_sample_rows, random_state=42)
        sampled = True
        sample_size = len(stats_df)

    duplicate_row_count = int(df.duplicated().sum())

    columns: list[ColumnProfile] = []
    numerical, categorical, datetime_cols, boolean_cols, constant_cols, target_candidates = (
        [], [], [], [], [], []
    )

    for col in df.columns:
        series = df[col]
        stats_series = stats_df[col]
        missing_count = int(series.isna().sum())
        missing_pct = round(100 * missing_count / n_rows, 2) if n_rows else 0.0
        unique_count = int(series.nunique(dropna=True))
        is_constant = unique_count <= 1
        inferred = "constant" if is_constant else _infer_column_type(series)

        cp = ColumnProfile(
            name=str(col), dtype=str(series.dtype), inferred_type=inferred,
            missing_count=missing_count, missing_pct=missing_pct,
            unique_count=unique_count, is_constant=is_constant,
            is_target_candidate=False,
        )

        if inferred == "numerical":
            numerical.append(str(col))
            non_null = stats_series.dropna()
            if not non_null.empty:
                cp.mean = float(non_null.mean())
                cp.std = float(non_null.std()) if len(non_null) > 1 else 0.0
                cp.min = float(non_null.min())
                cp.max = float(non_null.max())
                cp.skewness = float(non_null.skew()) if len(non_null) > 2 else 0.0
                cp.kurtosis = float(non_null.kurt()) if len(non_null) > 3 else 0.0
                cp.outlier_pct = _outlier_pct_iqr(non_null)
        elif inferred == "categorical":
            categorical.append(str(col))
            cardinality_ratio = unique_count / n_rows if n_rows else 0.0
            cp.cardinality_ratio = round(cardinality_ratio, 4)
            top = series.value_counts(dropna=True).head(10)
            cp.top_values = {str(k): int(v) for k, v in top.items()}
        elif inferred == "datetime":
            datetime_cols.append(str(col))
        elif inferred == "boolean":
            boolean_cols.append(str(col))
        elif inferred == "text":
            categorical.append(str(col))  # text columns are excluded from ML features downstream

        if is_constant:
            constant_cols.append(str(col))

        cardinality_ratio_for_target = cp.cardinality_ratio if inferred == "categorical" else None
        if not is_constant and _is_target_candidate(
            str(col), inferred, cardinality_ratio_for_target, n_rows, unique_count
        ):
            cp.is_target_candidate = True
            target_candidates.append(str(col))

        columns.append(cp)

    # Correlation matrix (numerical columns only, capped sample)
    correlation_matrix = None
    if len(numerical) >= 2:
        corr = stats_df[numerical].corr(numeric_only=True).round(4)
        correlation_matrix = {
            row: {col: (None if pd.isna(v) else float(v)) for col, v in corr.loc[row].items()}
            for row in corr.index
        }

    # Class imbalance signal — checked against the strongest target candidate
    class_imbalance = None
    if target_candidates:
        candidate = target_candidates[0]
        counts = df[candidate].value_counts(dropna=True)
        if len(counts) >= 2:
            majority_pct = float(round(100 * counts.iloc[0] / counts.sum(), 2))
            minority_pct = float(round(100 * counts.iloc[-1] / counts.sum(), 2))
            class_imbalance = {
                "column": candidate,
                "class_counts": {str(k): int(v) for k, v in counts.items()},
                "majority_class_pct": majority_pct,
                "minority_class_pct": minority_pct,
                "is_imbalanced": bool(majority_pct >= 80.0),
            }

    return DatasetProfile(
        dataset_name=dataset_name,
        n_rows=n_rows,
        n_columns=n_cols,
        memory_usage_bytes=int(df.memory_usage(deep=True).sum()),
        duplicate_row_count=duplicate_row_count,
        duplicate_row_pct=round(100 * duplicate_row_count / n_rows, 2) if n_rows else 0.0,
        columns=columns,
        numerical_columns=numerical,
        categorical_columns=categorical,
        datetime_columns=datetime_cols,
        boolean_columns=boolean_cols,
        constant_columns=constant_cols,
        target_column_candidates=target_candidates,
        correlation_matrix=correlation_matrix,
        class_imbalance=class_imbalance,
        sampled_for_stats=sampled,
        sample_size=sample_size,
        cleaning_notes=cleaning_notes or [],
    )
