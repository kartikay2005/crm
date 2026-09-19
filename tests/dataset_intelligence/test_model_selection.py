import pandas as pd
from services.dataset_intelligence.domain_knowledge import GENERAL_DOMAIN
from services.dataset_intelligence.model_registry import get_cached_registry
from services.dataset_intelligence.model_selection import (
    SCHEMA_MATCH_ACCEPT_THRESHOLD, select_model,
)
from services.dataset_intelligence.profiler import profile_dataset


def _registry():
    from core.config import get_settings
    return list(get_cached_registry(get_settings().model_registry_dir))


def test_full_match_selects_model():
    df = pd.DataFrame({
        "age": [45, 52, 61, 39, 70], "blood_pressure": [120, 135, 145, 118, 150],
        "heart_rate": [72, 80, 90, 68, 95], "cholesterol": [190, 210, 240, 180, 260],
    })
    profile = profile_dataset(df, "patients.csv")
    result = select_model("Healthcare", profile, _registry())
    assert result.selected_model is not None
    assert result.selected_model.name == "Heart Disease Risk Predictor"
    assert result.fallback_reason is None


def test_general_domain_never_selects_a_model():
    df = pd.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]})
    profile = profile_dataset(df, "ambiguous.csv")
    result = select_model(GENERAL_DOMAIN, profile, _registry())
    assert result.selected_model is None
    assert result.fallback_reason is not None


def test_below_threshold_never_forces_a_prediction():
    """The spec's core requirement, enforced as code: partial schema match
    below SCHEMA_MATCH_ACCEPT_THRESHOLD must never select a model, no
    matter how close it seems."""
    df = pd.DataFrame({"age": [45, 52, 61], "patient_id": [1, 2, 3]})  # only 1 of 4 required cols
    profile = profile_dataset(df, "partial.csv")
    result = select_model("Healthcare", profile, _registry())
    assert result.selected_model is None
    assert result.considered  # it WAS considered, just correctly rejected
    assert result.considered[0].match_pct < SCHEMA_MATCH_ACCEPT_THRESHOLD


def test_artifact_unavailable_reports_distinct_reason():
    """A model whose schema matches well but has no trained .joblib file
    (the Retail stub) must be reported as a distinct failure mode, not
    conflated with 'no domain match'."""
    df = pd.DataFrame({
        "product": ["A", "B"] * 5, "quantity": list(range(10)), "profit": [x * 1.5 for x in range(10)],
    })
    profile = profile_dataset(df, "retail.csv")
    result = select_model("Retail", profile, _registry())
    assert result.selected_model is None
    assert "isn't available yet" in (result.fallback_reason or "")


def test_no_models_for_domain_reports_distinct_reason():
    df = pd.DataFrame({"crop": ["wheat"] * 5, "yield": [1, 2, 3, 4, 5]})
    profile = profile_dataset(df, "farm.csv")
    result = select_model("Agriculture", profile, _registry())
    assert result.selected_model is None
    assert "No pretrained models are registered" in (result.fallback_reason or "")
