"""
End-to-end smoke test: seeds two tenants, creates users/roles, exercises
login, verifies tenant isolation (tenant A cannot see tenant B's users via
the ORM-level ContextVar filter), verifies audit chain integrity, and
verifies feedback recording. Run against SQLite for a fast dev-loop check;
run against real Postgres before considering this "verified" for RLS.

Usage: python3 scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import engine, SessionLocal, session_scope_for_tenant
from core.tenancy import install_tenant_query_filter, tenant_scope
from models.base import Base, install_insert_guard, new_uuid
from models.tenant import Tenant
from models.rbac import DEFAULT_ROLE_PERMISSIONS, Role, RolePermission, UserRole
from models.user import User
from services import auth as auth_service
from services import feedback as feedback_service
from services.audit import audit_event, verify_chain

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []


def check(name: str, condition: bool):
    results.append((name, condition))
    print(f"[{PASS if condition else FAIL}] {name}")


def main():
    Base.metadata.create_all(engine)
    install_tenant_query_filter(SessionLocal)
    install_insert_guard(SessionLocal)

    db = SessionLocal()

    # --- Seed two tenants ---
    tenant_a = Tenant(id=new_uuid(), slug="acme", display_name="Acme Corp")
    tenant_b = Tenant(id=new_uuid(), slug="globex", display_name="Globex Inc")
    tenant_a_id, tenant_b_id = tenant_a.id, tenant_b.id  # capture plain strings — see note below
    db.add_all([tenant_a, tenant_b])
    db.flush()

    # --- Seed roles for tenant A (must run inside a tenant context because
    #     Role/RolePermission/User are all tenant-scoped and the insert
    #     guard enforces this) ---
    with tenant_scope(tenant_a.id, "system", ("system",)):
        role = Role(id=new_uuid(), tenant_id=tenant_a.id, name="account_manager")
        db.add(role)
        db.flush()
        for perm in DEFAULT_ROLE_PERMISSIONS["account_manager"]:
            db.add(RolePermission(id=new_uuid(), tenant_id=tenant_a.id, role_id=role.id, permission=perm))
        db.flush()

        user_a = User(
            id=new_uuid(), tenant_id=tenant_a.id, email="jordan@acme.com",
            display_name="Jordan Lee",
            password_hash=auth_service.security.hash_password("CorrectHorseBattery9!"),
        )
        user_a_id = user_a.id  # capture plain string now — see note above on why
        db.add(user_a)
        db.flush()
        db.add(UserRole(id=new_uuid(), tenant_id=tenant_a.id, user_id=user_a_id, role_id=role.id))
        db.commit()

    check("Tenant A user created", user_a_id is not None)

    # NOTE ON CAPTURED IDS: from here on we use the plain string IDs
    # (tenant_a_id, tenant_b_id, user_a_id) rather than attribute access on
    # the ORM objects (tenant_a.id, user_a.id). This isn't just style —
    # after any db.rollback() in this script, SQLAlchemy expires all
    # attributes on tracked objects, and the *next* access of e.g. user_a.id
    # would trigger a lazy SELECT to refresh it. If that access happens
    # outside any tenant_scope() block (easy to do by accident when it's
    # buried inside a `with tenant_scope(tenant_a.id, user_a.id, ...)`
    # call — Python evaluates arguments before entering the context
    # manager), it fails the tenant-context guard. That's the system
    # working as intended (fail loud rather than silently querying
    # unscoped) — but it's a footgun worth avoiding in test code by simply
    # not depending on ORM attribute access for values we already know.

    # --- Login flow ---
    result = auth_service.authenticate(
        db, tenant_a_id, "jordan@acme.com", "CorrectHorseBattery9!",
        ip_address="127.0.0.1", user_agent="pytest",
    )
    db.commit()
    check("Login succeeds with correct password", result.access_token is not None)
    check("Login returns correct role", "account_manager" in result.roles)

    # --- Wrong password ---
    try:
        auth_service.authenticate(
            db, tenant_a_id, "jordan@acme.com", "wrong-password",
            ip_address="127.0.0.1", user_agent="pytest",
        )
        check("Wrong password rejected", False)
    except auth_service.InvalidCredentials:
        check("Wrong password rejected", True)
    db.commit()

    # --- Lockout after N failed attempts ---
    from core.config import get_settings
    max_attempts = get_settings().max_failed_login_attempts
    for _ in range(max_attempts):
        try:
            auth_service.authenticate(
                db, tenant_a_id, "jordan@acme.com", "wrong-password",
                ip_address="127.0.0.1", user_agent="pytest",
            )
        except auth_service.InvalidCredentials:
            pass
        except auth_service.AccountLocked:
            break
    db.commit()
    try:
        auth_service.authenticate(
            db, tenant_a_id, "jordan@acme.com", "CorrectHorseBattery9!",  # even correct pw
            ip_address="127.0.0.1", user_agent="pytest",
        )
        check("Account locks after repeated failures", False)
    except auth_service.AccountLocked:
        check("Account locks after repeated failures", True)
    db.commit()

    # --- Tenant isolation: query users while in tenant B context should not
    # see tenant A's user. Uses session_scope_for_tenant (fresh session per
    # tenant) — the REQUIRED pattern, matching how the real API request path
    # behaves (fresh Session per request, established before tenant is known).
    with session_scope_for_tenant(tenant_b_id, "system", ("system",)) as db_b:
        from sqlalchemy import select
        visible = db_b.execute(select(User)).scalars().all()
        check("Tenant B sees zero users from Tenant A (fresh session, ORM filter)", len(visible) == 0)

    with session_scope_for_tenant(tenant_a_id, "system", ("system",)) as db_a:
        from sqlalchemy import select
        visible = db_a.execute(select(User)).scalars().all()
        check("Tenant A sees its own user (fresh session, ORM filter)", len(visible) == 1)

    # --- Regression test: documents the identity-map footgun that
    # session_scope_for_tenant exists to prevent. A single Session reused
    # across two tenant_scope() blocks WILL leak an already-loaded row via
    # Session.get(), because identity-map hits never issue SQL and therefore
    # never pass through the tenant filter. This is expected, documented
    # behavior — the fix is "always use session_scope_for_tenant", not
    # "make Session.get() safe", which SQLAlchemy's architecture doesn't
    # allow. We assert the leak happens so a future SQLAlchemy upgrade that
    # silently changes this behavior would fail the test loudly.
    with tenant_scope(tenant_a_id, "system", ("system",)):
        from sqlalchemy import select
        _ = db.execute(select(User).where(User.id == user_a_id)).scalar_one()  # loads into identity map
    with tenant_scope(tenant_b_id, "system", ("system",)):
        leaked = db.get(User, user_a_id)  # same shared `db` Session as above — unsafe on purpose
        check(
            "KNOWN LIMITATION confirmed: shared Session + db.get() leaks across "
            "tenant_scope() switches (this is why session_scope_for_tenant exists)",
            leaked is not None,
        )

    # --- Cross-tenant insert guard ---
    with tenant_scope(tenant_a_id, "system", ("system",)):
        try:
            bad_user = User(
                id=new_uuid(), tenant_id=tenant_b_id,  # wrong tenant on purpose
                email="hacker@evil.com", display_name="Hacker",
            )
            db.add(bad_user)
            db.flush()
            check("Insert guard blocks cross-tenant insert", False)
        except PermissionError:
            check("Insert guard blocks cross-tenant insert", True)
            db.rollback()

    # --- Audit chain ---
    with tenant_scope(tenant_a_id, "system", ("system",)):
        audit_event(db, "test.event_1", payload={"n": 1})
        audit_event(db, "test.event_2", payload={"n": 2})
        audit_event(db, "test.event_3", payload={"n": 3})
        db.commit()
        valid, bad_idx = verify_chain(db, tenant_a_id)
        check("Audit chain verifies as intact", valid)

        # Tamper with a row and confirm detection
        from models.audit import AuditLog
        from sqlalchemy import select
        row = db.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant_a_id, AuditLog.event_type == "test.event_2")
        ).scalar_one()
        row.payload = {"n": 999}  # tamper
        db.commit()
        valid2, bad_idx2 = verify_chain(db, tenant_a_id)
        check("Audit chain detects tampering", not valid2)

    # --- Feedback loop ---
    with tenant_scope(tenant_a_id, user_a_id, ("account_manager",)):
        reco_id = feedback_service.record_recommendation(
            db, seller_id="seller-001", user_id=user_a_id,
            recommendation_type="prioritize", action="contact_seller",
            reason="High return rate", score=72.5,
            features={"category": "electronics", "return_rate": 0.15},
            model_version="test/1.0",
        )
        db.commit()
        check("Recommendation persisted", reco_id is not None)

        feedback_service.record_feedback(
            db, recommendation_id=reco_id, user_id=user_a_id, outcome="accepted",
        )
        db.commit()
        check("Feedback recorded without error", True)

    db.close()

    # --- Summary ---
    print()
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    print(f"{passed}/{total} checks passed")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
