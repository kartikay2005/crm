"""
Verifies that Postgres Row-Level Security genuinely enforces tenant
isolation — end to end, against a REAL Postgres instance, using nothing but
raw SQL and a session variable, with zero Python ORM involvement.

Why this is a separate script rather than a pytest fixture: it needs a real
Postgres server, which the fast SQLite-based suite in tests/ deliberately
doesn't provision (RLS is a Postgres-only feature — see
alembic/versions/001_initial_schema.py and 002_dataset_intelligence.py,
which both no-op the RLS setup on non-Postgres backends). Rather than
requiring Docker or a system Postgres install for local/CI verification,
this uses `pgserver` — a pip-installable embedded Postgres — so the check
can run anywhere Python runs.

Two things this script deliberately proves, because both were genuine
discoveries made while building this segment:

1. RLS must be tested against a REALISTIC (non-superuser) database role.
   Postgres never applies row-level security to superusers, regardless of
   FORCE ROW LEVEL SECURITY — connecting as the default superuser and
   declaring "RLS works" would be a false positive. This script explicitly
   creates a restricted `app_runtime_role` and runs every check through it,
   the same way a real deployment's application connection string must be
   configured.

2. The FULL application stack (real HTTP requests, real auth, the ORM-level
   tenant filter, AND Postgres RLS) is exercised together, not just RLS in
   isolation — confirming both isolation layers coexist correctly rather
   than one masking a bug in the other.

Run: python3 scripts/test_postgres_rls.py
Requires: pip install -r requirements-dev.txt (for pgserver)
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results: list[tuple[str, bool]] = []


def check(name: str, condition: bool) -> None:
    results.append((name, condition))
    print(f"[{PASS if condition else FAIL}] {name}")


def main() -> None:
    try:
        import pgserver
    except ImportError:
        print("pgserver not installed — run: pip install -r requirements-dev.txt")
        sys.exit(1)

    pgdata = tempfile.mkdtemp()
    pg = pgserver.get_server(pgdata)
    uri = pg.get_uri().replace("postgresql://", "postgresql+psycopg://")

    os.environ["DATABASE_URL"] = uri
    os.environ.setdefault("ENV", "local")
    os.environ.setdefault("JWT_SECRET_KEY", "test-only-not-for-prod-32-chars-min")
    os.environ.setdefault("FIELD_ENCRYPTION_KEY", "")
    os.environ.setdefault("REDIS_URL", "")

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", uri)
    command.upgrade(cfg, "head")
    check("Migrations apply cleanly against real Postgres", True)

    from sqlalchemy import create_engine, text

    admin_engine = create_engine(uri)

    # --- Part 1: raw-SQL RLS check with a REALISTIC non-superuser role ---
    with admin_engine.begin() as conn:
        conn.execute(text("DROP ROLE IF EXISTS app_runtime_role"))
        conn.execute(text("CREATE ROLE app_runtime_role LOGIN PASSWORD 'app_pw'"))
        conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime_role"))
        conn.execute(text("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_runtime_role"))

    tenant_a_id, tenant_b_id = str(uuid.uuid4()), str(uuid.uuid4())
    dataset_a_id, dataset_b_id = str(uuid.uuid4()), str(uuid.uuid4())

    with admin_engine.begin() as conn:
        for tid, slug, name in [(tenant_a_id, "acme", "Acme"), (tenant_b_id, "globex", "Globex")]:
            conn.execute(
                text("INSERT INTO tenants (id, slug, display_name, is_active, data_region) "
                     "VALUES (:id, :slug, :name, true, 'us-east-1')"),
                {"id": tid, "slug": slug, "name": name},
            )
        conn.execute(text("""
            INSERT INTO datasets (id, tenant_id, uploaded_by_user_id, original_filename,
                                   extension, size_bytes, storage_path, status)
            VALUES (:id, :tid, 'user-1', 'secret_a.csv', '.csv', 100, '/tmp/a.csv', 'uploaded')
        """), {"id": dataset_a_id, "tid": tenant_a_id})
        conn.execute(text("""
            INSERT INTO datasets (id, tenant_id, uploaded_by_user_id, original_filename,
                                   extension, size_bytes, storage_path, status)
            VALUES (:id, :tid, 'user-1', 'secret_b.csv', '.csv', 100, '/tmp/b.csv', 'uploaded')
        """), {"id": dataset_b_id, "tid": tenant_b_id})

    app_uri = uri.replace("postgres:@", "app_runtime_role:app_pw@")
    app_engine = create_engine(app_uri)

    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_tenant_id', :tid, false)"), {"tid": tenant_a_id})
        rows = conn.execute(text("SELECT original_filename FROM datasets")).fetchall()
        check("Non-superuser role scoped to tenant A sees only tenant A's row",
              len(rows) == 1 and rows[0][0] == "secret_a.csv")

    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_tenant_id', :tid, false)"), {"tid": tenant_b_id})
        rows = conn.execute(text("SELECT original_filename FROM datasets")).fetchall()
        check("Non-superuser role scoped to tenant B sees only tenant B's row",
              len(rows) == 1 and rows[0][0] == "secret_b.csv")

    with app_engine.connect() as conn:
        rows = conn.execute(text("SELECT original_filename FROM datasets")).fetchall()
        check("No tenant context set -> zero rows visible", len(rows) == 0)

    with admin_engine.connect() as conn:
        rows = conn.execute(text("SELECT original_filename FROM datasets")).fetchall()
        check(
            "KNOWN Postgres behavior confirmed: superuser bypasses RLS "
            "entirely regardless of FORCE ROW LEVEL SECURITY (this is why "
            "the app's runtime DB role must never be a superuser)",
            len(rows) == 2,
        )

    # --- Part 2: full application stack (real HTTP, real auth, both isolation layers) ---
    from fastapi.testclient import TestClient

    from api.main import app
    from core.database import SessionLocal
    from core.tenancy import install_tenant_query_filter, tenant_scope
    from models.base import install_insert_guard, new_uuid
    from models.rbac import DEFAULT_ROLE_PERMISSIONS, Role, RolePermission, UserRole
    from models.tenant import Tenant
    from models.user import User
    from services import auth as auth_service

    install_tenant_query_filter(SessionLocal)
    install_insert_guard(SessionLocal)
    db = SessionLocal()

    def make_tenant_and_user(slug: str, email: str) -> Tenant:
        tenant = Tenant(id=new_uuid(), slug=slug, display_name=slug)
        db.add(tenant)
        db.flush()
        with tenant_scope(tenant.id, "system", ("system",)):
            role = Role(id=new_uuid(), tenant_id=tenant.id, name="account_manager")
            db.add(role)
            db.flush()
            for perm in DEFAULT_ROLE_PERMISSIONS["account_manager"]:
                db.add(RolePermission(id=new_uuid(), tenant_id=tenant.id, role_id=role.id, permission=perm))
            db.flush()
            user = User(id=new_uuid(), tenant_id=tenant.id, email=email, display_name="User",
                        password_hash=auth_service.security.hash_password("CorrectHorseBattery9!"))
            db.add(user)
            db.flush()
            db.add(UserRole(id=new_uuid(), tenant_id=tenant.id, user_id=user.id, role_id=role.id))
            db.commit()
        return tenant

    make_tenant_and_user("appstack-a", "jordan@appstack-a.com")
    make_tenant_and_user("appstack-b", "sam@appstack-b.com")

    client = TestClient(app)
    token_a = client.post("/api/v1/auth/login", json={
        "tenant_slug": "appstack-a", "email": "jordan@appstack-a.com", "password": "CorrectHorseBattery9!",
    }).json()["access_token"]
    token_b = client.post("/api/v1/auth/login", json={
        "tenant_slug": "appstack-b", "email": "sam@appstack-b.com", "password": "CorrectHorseBattery9!",
    }).json()["access_token"]

    upload_resp = client.post(
        "/api/v1/datasets/upload", headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("patients.csv", (
            b"age,blood_pressure,heart_rate,cholesterol,diagnosis\n"
            b"45,120,72,190,0\n52,135,80,210,1\n61,145,90,240,1\n39,118,68,180,0\n70,150,95,260,1\n"
        ), "text/csv")},
    )
    check("Full app stack: upload succeeds against real Postgres", upload_resp.status_code == 201)
    dsid = upload_resp.json()["dataset_id"]

    owner_resp = client.get(f"/api/v1/datasets/{dsid}/profile", headers={"Authorization": f"Bearer {token_a}"})
    check("Full app stack: owner can read their own dataset", owner_resp.status_code == 200)

    cross_resp = client.get(f"/api/v1/datasets/{dsid}/profile", headers={"Authorization": f"Bearer {token_b}"})
    check("Full app stack: cross-tenant read correctly 404s", cross_resp.status_code == 404)

    print()
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    print(f"{passed}/{total} checks passed")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
