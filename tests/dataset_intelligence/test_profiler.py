import pandas as pd
from services.dataset_intelligence.profiler import profile_dataset


def test_detects_constant_column():
    df = pd.DataFrame({"a": [1, 2, 3, 4], "constant": [5, 5, 5, 5]})
    profile = profile_dataset(df, "test.csv")
    assert "constant" in profile.constant_columns


def test_detects_duplicate_rows():
    df = pd.DataFrame({"a": [1, 1, 2, 3], "b": [1, 1, 2, 3]})
    profile = profile_dataset(df, "test.csv")
    assert profile.duplicate_row_count == 1  # one row is a dupe of another


def test_detects_missing_values():
    df = pd.DataFrame({"a": [1, None, 3, None]})
    profile = profile_dataset(df, "test.csv")
    col = next(c for c in profile.columns if c.name == "a")
    assert col.missing_count == 2
    assert col.missing_pct == 50.0


def test_correlation_matrix_detects_strong_correlation():
    df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [2, 4, 6, 8, 10]})  # b = 2a, perfect correlation
    profile = profile_dataset(df, "test.csv")
    assert profile.correlation_matrix is not None
    assert abs(profile.correlation_matrix["a"]["b"] - 1.0) < 0.01


def test_class_imbalance_detected():
    df = pd.DataFrame({
        "id": range(100),
        "churn": [1] * 90 + [0] * 10,  # 90/10 imbalanced binary target
    })
    profile = profile_dataset(df, "test.csv")
    assert profile.class_imbalance is not None
    assert profile.class_imbalance["is_imbalanced"] is True
    assert profile.class_imbalance["majority_class_pct"] == 90.0


def test_id_column_excluded_from_target_candidates():
    df = pd.DataFrame({"id": range(20), "diagnosis": [0, 1] * 10})
    profile = profile_dataset(df, "test.csv")
    assert "id" not in profile.target_column_candidates
    assert "diagnosis" in profile.target_column_candidates


def test_outlier_detection_iqr():
    # Genuine spread across the distribution (not the degenerate case where
    # 19/20 values are identical) so Q1 != Q3 and the IQR fence is
    # well-defined. profiler.py deliberately returns 0.0 when IQR==0 rather
    # than computing an unstable fence — see profiler.py's _outlier_pct_iqr
    # for that guard; this test exercises the normal, non-degenerate path.
    values = list(range(1, 20)) + [1000]  # spread 1-19, one extreme outlier
    df = pd.DataFrame({"a": values})
    profile = profile_dataset(df, "test.csv")
    col = next(c for c in profile.columns if c.name == "a")
    assert col.outlier_pct is not None and col.outlier_pct > 0


def test_json_serializable_output():
    """Regression test for the numpy-scalar-leak bug class that hit this
    module in Segments 1 and 2 (np.bool_ breaking JSON, np.float64 leaking
    into logs). Every value in the profile dict must be a plain Python
    type, not a numpy scalar."""
    import json
    df = pd.DataFrame({
        "num": [1.5, 2.5, 3.5, None],
        "cat": ["a", "b", "a", "c"],
        "target": [1, 0, 1, 0],
    })
    profile = profile_dataset(df, "test.csv")
    json.dumps(profile.to_dict())  # raises TypeError if any numpy scalar leaked through
