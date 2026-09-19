import json
import tempfile
from pathlib import Path

import joblib
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from services.dataset_intelligence.model_registry import load_registry
from services.dataset_intelligence.prediction import PredictionError, predict_dataset


def test_feature_count_mismatch_raises_specific_diagnostic():
    """Regression test for the Segment 4 bug: a model trained on N
    features but whose metadata declares a different number of
    required_features must fail with a SPECIFIC, actionable error — not a
    generic sklearn traceback, and not (worse) a silently wrong
    prediction. Reproduces the exact bug: trains on 3 features, declares
    only 2 as required."""
    with tempfile.TemporaryDirectory() as tmpdir:
        domain_dir = Path(tmpdir) / "testdomain"
        domain_dir.mkdir()

        # Train on 3 features...
        X = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [5, 4, 3, 2, 1], "c": [1, 1, 2, 2, 3]})
        y = [0, 1, 0, 1, 0]
        model = LogisticRegression().fit(X, y)
        joblib.dump(model, domain_dir / "mismatch_model.joblib")

        # ...but metadata only declares 2 as required (the real bug shape)
        metadata = {
            "name": "Mismatch Test Model", "version": "1.0.0", "domain": "TestDomain",
            "problem_type": "binary_classification", "target_variable": "y",
            "required_features": [{"name": "a", "dtype": "numerical"}, {"name": "b", "dtype": "numerical"}],
            "optional_features": [], "allowed_missing_pct": 10.0,
        }
        (domain_dir / "mismatch_model.json").write_text(json.dumps(metadata))

        registry = load_registry(tmpdir)
        assert len(registry) == 1
        model_meta = registry[0]

        df = pd.DataFrame({"a": [1, 2, 3], "b": [3, 2, 1]})
        with pytest.raises(PredictionError) as exc_info:
            predict_dataset(df, model_meta)

        # Must be the SPECIFIC diagnostic, not a generic sklearn error
        assert "trained on" in str(exc_info.value)
        assert "registry metadata declares" in str(exc_info.value)
