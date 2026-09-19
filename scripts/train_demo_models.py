"""
Trains two small, genuinely-real demo models and registers them in
model_registry/, so Segment 3's schema-matching and model-selection logic
has something real to select — not just metadata-only stubs.

These are intentionally simple (LogisticRegression on synthetic data) —
the point of this script is to prove the registry/schema-matching/selection
loop end-to-end, not to ship production-accuracy models. Segment 4 owns
the real prediction engine; this script exists to unblock testing it.

Run: python3 scripts/train_demo_models.py
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

ROOT = Path(__file__).resolve().parent.parent / "model_registry"
RNG = np.random.default_rng(42)


def _write_metadata(domain_dir: Path, model_filename: str, metadata: dict) -> None:
    domain_dir.mkdir(parents=True, exist_ok=True)
    (domain_dir / f"{model_filename}.json").write_text(json.dumps(metadata, indent=2))


def train_healthcare_model() -> None:
    n = 800
    age = RNG.normal(50, 15, n).clip(18, 90)
    blood_pressure = RNG.normal(125, 18, n).clip(80, 200) + (age - 50) * 0.3
    heart_rate = RNG.normal(75, 12, n).clip(45, 130)
    cholesterol = RNG.normal(200, 35, n).clip(120, 350) + (age - 50) * 0.5

    risk_score = (
        0.03 * (age - 50) + 0.02 * (blood_pressure - 120)
        + 0.015 * (cholesterol - 200) + RNG.normal(0, 1, n)
    )
    diagnosis = (risk_score > np.percentile(risk_score, 55)).astype(int)

    X = pd.DataFrame({
        "age": age, "blood_pressure": blood_pressure,
        "heart_rate": heart_rate, "cholesterol": cholesterol,
    })
    X_train, X_test, y_train, y_test = train_test_split(X, diagnosis, test_size=0.2, random_state=42)

    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train)
    accuracy = accuracy_score(y_test, model.predict(X_test))

    domain_dir = ROOT / "healthcare"
    domain_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, domain_dir / "heart_model.joblib")

    _write_metadata(domain_dir, "heart_model", {
        "name": "Heart Disease Risk Predictor",
        "version": "1.0.0",
        "domain": "Healthcare",
        "problem_type": "binary_classification",
        "target_variable": "diagnosis",
        "required_features": [
            {"name": "age", "dtype": "numerical"},
            {"name": "blood_pressure", "dtype": "numerical"},
            {"name": "heart_rate", "dtype": "numerical"},
            {"name": "cholesterol", "dtype": "numerical"},
        ],
        "optional_features": [{"name": "patient_id", "dtype": "numerical"}],
        "allowed_missing_pct": 10.0,
        "accuracy": round(float(accuracy), 3),
        "training_date": "2026-07-21",
        "description": "Predicts heart disease risk from age, blood pressure, "
                        "heart rate, and cholesterol. Demo model trained on "
                        "synthetic data for pipeline validation.",
    })
    print(f"Trained Heart Disease Risk Predictor — accuracy={accuracy:.3f}")


def train_finance_model() -> None:
    n = 800
    income = RNG.normal(50000, 15000, n).clip(15000, 150000)
    credit_score = RNG.normal(650, 70, n).clip(300, 850)
    loan_amount = RNG.normal(18000, 7000, n).clip(1000, 60000)
    debt_ratio = RNG.beta(2, 5, n)
    interest_rate = 4 + (850 - credit_score) / 850 * 15 + RNG.normal(0, 1, n)
    interest_rate = interest_rate.clip(3, 30)

    risk_score = (
        -0.00002 * (income - 50000) - 0.01 * (credit_score - 650)
        + 3.0 * debt_ratio + 0.15 * (interest_rate - 10) + RNG.normal(0, 1, n)
    )
    default = (risk_score > np.percentile(risk_score, 65)).astype(int)

    X = pd.DataFrame({
        "income": income, "credit_score": credit_score, "loan_amount": loan_amount,
        "debt_ratio": debt_ratio,
    })
    X_train, X_test, y_train, y_test = train_test_split(X, default, test_size=0.2, random_state=42)

    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train)
    accuracy = accuracy_score(y_test, model.predict(X_test))

    domain_dir = ROOT / "finance"
    domain_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, domain_dir / "loan_model.joblib")

    _write_metadata(domain_dir, "loan_model", {
        "name": "Loan Default Predictor",
        "version": "1.0.0",
        "domain": "Finance",
        "problem_type": "binary_classification",
        "target_variable": "default",
        "required_features": [
            {"name": "income", "dtype": "numerical"},
            {"name": "credit_score", "dtype": "numerical"},
            {"name": "loan_amount", "dtype": "numerical"},
            {"name": "debt_ratio", "dtype": "numerical"},
        ],
        "optional_features": [
            {"name": "interest_rate", "dtype": "numerical"},
            {"name": "customer_id", "dtype": "numerical"},
        ],
        "allowed_missing_pct": 10.0,
        "accuracy": round(float(accuracy), 3),
        "training_date": "2026-07-21",
        "description": "Predicts loan default risk from income, credit score, "
                        "loan amount, and debt ratio. Demo model trained on "
                        "synthetic data for pipeline validation.",
    })
    print(f"Trained Loan Default Predictor — accuracy={accuracy:.3f}")


def register_stub_entries() -> None:
    """A couple of metadata-only entries with no trained artifact yet, to
    prove the registry/selection logic correctly reports 'matches schema
    but model file not available' rather than crashing or silently
    predicting with nothing."""
    _write_metadata(ROOT / "retail", "sales_model", {
        "name": "Retail Revenue Forecaster",
        "version": "0.1.0-stub",
        "domain": "Retail",
        "problem_type": "regression",
        "target_variable": "revenue",
        "required_features": [
            {"name": "product", "dtype": "categorical"},
            {"name": "quantity", "dtype": "numerical"},
            {"name": "profit", "dtype": "numerical"},
        ],
        "optional_features": [],
        "allowed_missing_pct": 10.0,
        "accuracy": None,
        "training_date": None,
        "description": "Not yet trained — metadata-only registry entry for testing "
                        "the 'schema matches but artifact unavailable' fallback path.",
    })
    print("Registered stub entry: Retail Revenue Forecaster (no artifact)")


if __name__ == "__main__":
    train_healthcare_model()
    train_finance_model()
    register_stub_entries()
    print(f"\nRegistry populated at {ROOT}")
