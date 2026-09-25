"""
Seeds a ready-to-explore demo tenant for showing this app to someone who
didn't build it (recruiters, interviewers, a teammate evaluating the repo).

Without this, a fresh deploy starts completely empty — no tenant, no user,
no datasets. This creates one admin-level user with credentials printed to
stdout, and pre-loads three already-analyzed sample datasets (Healthcare,
Finance, Retail) — including two real SHAP-explained predictions.

Run locally, after the schema exists:

    python3 -m alembic upgrade head     # if you haven't already
    python3 scripts/seed_demo.py

On a deployed instance (Render free tier and similar), prefer triggering
this over HTTP instead — see docs/DEPLOYMENT.md's "Seeding the live demo"
section for why: running this as a separate process alongside the live
server duplicates a lot of memory and can be very slow on a throttled CPU
the first time SHAP's numba-accelerated code JIT-compiles.

Safe to re-run: skips seeding if the "demo" tenant already exists.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import engine, SessionLocal
from core.tenancy import install_tenant_query_filter
from models.base import Base, install_insert_guard
from services.demo_seed import run_seed_demo


def main() -> None:
    Base.metadata.create_all(engine)
    install_tenant_query_filter(SessionLocal)
    install_insert_guard(SessionLocal)
    db = SessionLocal()

    result = run_seed_demo(db)

    if result["already_seeded"]:
        print(f"Tenant 'demo' already exists (id={result['tenant_id']}) — skipping.")
        return

    print("Seeded tenant 'demo'. Login with:")
    print(f"  workspace: {result['tenant_slug']}")
    print(f"  email:     {result['email']}")
    print(f"  password:  {result['password']}")
    print()
    for d in result["datasets"]:
        if "error" in d:
            print(f"  ! {d['filename']} failed validation: {d['error']}")
        else:
            outcome = "real prediction + SHAP explanation" if d["got_real_prediction"] else "insights-only (honest fallback)"
            print(f"  + {d['filename']}: detected as {d['domain']} — {outcome}")
    print("\nDemo ready.")


if __name__ == "__main__":
    main()
