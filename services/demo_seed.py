"""
Core logic for seeding the recruiter/reviewer demo tenant — see
scripts/seed_demo.py for the CLI entry point and its full explanation of
what this creates and why.

This lives in services/ (not just in the script) so it can also be called
in-process by api.routes.health's POST /_seed-demo endpoint. That matters
specifically because of what this function touches: `services.dataset_
intelligence.pipeline` imports `shap`, which imports `numba` — and numba
JIT-compiles its accelerated code the first time it actually runs, not at
import time. That one-time compile is slow on a CPU-throttled host. Running
this as a *separate* `python3 scripts/seed_demo.py` process (as an earlier
version of docker-entrypoint.sh did) means paying that import cost twice —
once in the gunicorn worker (which already imports the same pipeline to
serve real requests) and once again in the separate script process, at the
same time, competing for the same limited memory. Calling this function
in-process from an HTTP request instead reuses the worker's already-loaded
modules: no duplicate memory, and no risk of hitting a platform's
deploy-time port-scan timeout, since the server is already live by the
time anyone triggers it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.security import hash_password
from core.tenancy import tenant_scope
from models.base import new_uuid
from models.rbac import DEFAULT_ROLE_PERMISSIONS, Role, RolePermission, UserRole
from models.tenant import Tenant
from models.user import User
from services.dataset_intelligence.pipeline import DatasetIntelligenceError, ingest_and_analyze

DEMO_SLUG = "demo"
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "RecruiterDemo2026!"

RNG = np.random.default_rng(7)


def _healthcare_csv() -> bytes:
    """Columns match the Heart Disease Risk Predictor's required schema
    exactly (age, blood_pressure, heart_rate, cholesterol) so this one
    gets a real prediction + SHAP explanation, not the fallback path."""
    n = 220
    age = RNG.integers(28, 82, n)
    cholesterol = (180 + (age - 28) * 1.1 + RNG.normal(0, 18, n)).clip(140, 340)
    blood_pressure = (105 + (age - 28) * 0.55 + RNG.normal(0, 10, n)).clip(90, 190)
    heart_rate = RNG.normal(74, 11, n).clip(50, 115)
    bmi = RNG.normal(27, 4.5, n).clip(17, 44)
    risk = 0.03 * (age - 50) + 0.015 * (cholesterol - 200) + 0.02 * (blood_pressure - 120) - 0.9
    diagnosis = (RNG.uniform(0, 1, n) < 1 / (1 + np.exp(-risk))).astype(int)
    df = pd.DataFrame({
        "patient_id": np.arange(1, n + 1), "age": age,
        "blood_pressure": blood_pressure.round(1), "heart_rate": heart_rate.round(1),
        "cholesterol": cholesterol.round(1), "bmi": bmi.round(1), "diagnosis": diagnosis,
    })
    return df.to_csv(index=False).encode()


def _finance_csv() -> bytes:
    """Columns match the Loan Default Predictor's required schema exactly
    (income, credit_score, loan_amount, debt_ratio) — also gets a real
    prediction, so the demo shows two working models, not one."""
    n = 260
    income = RNG.gamma(6, 9000, n).clip(18000, 220000)
    credit_score = RNG.normal(680, 65, n).clip(400, 850)
    loan_amount = (income * RNG.uniform(0.15, 0.55, n)).round(-2)
    debt_ratio = RNG.beta(2, 5, n)
    interest_rate = (14 - (credit_score - 400) / 45).clip(3.5, 18)
    default_prob = (0.4 * debt_ratio + 0.3 * (1 - (credit_score - 400) / 450) - 0.15).clip(0.02, 0.9)
    default = (RNG.uniform(0, 1, n) < default_prob).astype(int)
    df = pd.DataFrame({
        "customer_id": np.arange(1, n + 1), "income": income.round(2),
        "credit_score": credit_score.round(0).astype(int), "loan_amount": loan_amount,
        "debt_ratio": debt_ratio.round(3), "interest_rate": interest_rate.round(2), "default": default,
    })
    return df.to_csv(index=False).encode()


def _retail_csv() -> bytes:
    """No trained Retail model exists (stub entry only) — this one
    deliberately shows the honest-fallback path: domain detection,
    quality/readiness scoring, and heuristic recommendations, with no
    forced prediction."""
    n = 180
    categories = RNG.choice(["Electronics", "Apparel", "Home & Kitchen", "Grocery", "Toys"], n)
    unit_price = RNG.gamma(3, 12, n).clip(3, 400)
    quantity_sold = RNG.poisson(35, n)
    inventory_level = RNG.poisson(60, n)
    revenue = (unit_price * quantity_sold).round(2)
    return_rate = RNG.beta(2, 20, n).round(3)
    df = pd.DataFrame({
        "product_id": np.arange(1, n + 1), "category": categories,
        "unit_price": unit_price.round(2), "quantity_sold": quantity_sold,
        "inventory_level": inventory_level, "revenue": revenue, "return_rate": return_rate,
    })
    return df.to_csv(index=False).encode()


def run_seed_demo(db: Session) -> dict:
    """Idempotent: if the demo tenant already exists, returns immediately
    with already_seeded=True instead of creating duplicates. Caller owns
    the Session's lifecycle (this function does not close it)."""
    existing = db.execute(select(Tenant).where(Tenant.slug == DEMO_SLUG)).scalar_one_or_none()
    if existing:
        return {"already_seeded": True, "tenant_id": existing.id}

    tenant = Tenant(id=new_uuid(), slug=DEMO_SLUG, display_name="Demo Company")
    db.add(tenant)
    db.flush()

    with tenant_scope(tenant.id, "system", ("system",)):
        role = Role(id=new_uuid(), tenant_id=tenant.id, name="tenant_admin")
        db.add(role)
        db.flush()
        for perm in DEFAULT_ROLE_PERMISSIONS["tenant_admin"]:
            db.add(RolePermission(id=new_uuid(), tenant_id=tenant.id, role_id=role.id, permission=perm))

        user = User(
            id=new_uuid(), tenant_id=tenant.id, email=DEMO_EMAIL,
            display_name="Demo Recruiter Account",
            password_hash=hash_password(DEMO_PASSWORD), is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(id=new_uuid(), tenant_id=tenant.id, user_id=user.id, role_id=role.id))
        db.commit()

    samples = [
        ("patient_risk_sample.csv", _healthcare_csv()),
        ("loan_applicants_sample.csv", _finance_csv()),
        ("retail_inventory_sample.csv", _retail_csv()),
    ]
    dataset_results = []
    with tenant_scope(tenant.id, user.id, ("tenant_admin",)):
        for filename, content in samples:
            try:
                dataset, profile, result = ingest_and_analyze(db, filename, content, user.id)
                db.commit()
                dataset_results.append({
                    "filename": filename,
                    "domain": result.domain_result.top_domain,
                    "got_real_prediction": result.prediction is not None,
                })
            except DatasetIntelligenceError as exc:
                db.rollback()
                dataset_results.append({"filename": filename, "error": exc.message})

    return {
        "already_seeded": False,
        "tenant_slug": DEMO_SLUG,
        "email": DEMO_EMAIL,
        "password": DEMO_PASSWORD,
        "datasets": dataset_results,
    }
