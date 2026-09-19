# Enterprise Hardening — Progress Checkpoint

Saved: 2026-07-21

## What's built (25 files)

### Core foundation — DONE
- `core/config.py` — Pydantic settings, secrets externalized, prod validators
- `core/security.py` — Argon2 hashing, JWT with refresh, secure tokens, timing-safe compare
- `core/database.py` — Connection pool, pre-ping, health check, statement timeout
- `core/tenancy.py` — TenantContext ContextVar, query-time filter, insert-time guard
- `core/logging_config.py` — Structured JSON logs, PII redaction, tenant auto-tagging
- `core/metrics.py` — Prometheus counters/histograms (cardinality-safe)
- `core/crypto.py` — Field-level Fernet encryption with rotation

### Models — DONE
- `models/base.py` — Declarative base + tenant enforcement hooks
- `models/tenant.py` — Tenant (data residency, SSO config)
- `models/user.py` — User + Session (MFA-ready, lockout state, SSO subject)
- `models/rbac.py` — Role, RolePermission, UserRole + Perm constants + defaults
- `models/audit.py` — Tamper-evident hash-chained audit log
- `models/feedback.py` — Recommendation + RecommendationFeedback
- `models/config.py` — CategoryThreshold + LearnedWeight

### Services — DONE
- `services/auth.py` — Login, MFA (TOTP), refresh rotation w/ theft detection, lockout
- `services/audit.py` — audit_event() + chain verifier + request-scoped correlation
- `services/feedback.py` — Record, aggregate to LearnedWeight, build training dataset
- `services/data_source.py` — Protocol + SQL / REST / CSV adapters
- `services/prioritization_v2.py` — Category-aware, learned-weight-adjusted, audit-trailing

## What's NOT built yet (pick up here tomorrow)

### API layer (next up)
- [ ] `api/main.py` — FastAPI app, middleware stack (CORS, security headers, rate limit, request ID, tenant context)
- [ ] `api/deps.py` — get_current_user, get_current_tenant, require_permission
- [ ] `api/middleware.py` — request logging, metrics, tenant extraction from JWT
- [ ] `api/routes/auth.py` — POST /login, /refresh, /logout, /mfa/enroll, /mfa/confirm
- [ ] `api/routes/sellers.py` — GET /sellers (paginated), /sellers/{id}
- [ ] `api/routes/prioritization.py` — GET /prioritization
- [ ] `api/routes/feedback.py` — POST /feedback
- [ ] `api/routes/admin.py` — CRUD for CategoryThreshold, user/role management
- [ ] `api/routes/health.py` — /healthz, /readyz, /metrics

### Database migrations
- [ ] `alembic.ini`
- [ ] `alembic/env.py`
- [ ] `alembic/versions/001_initial.py` — Create all tables + indexes + Postgres RLS policies

### Ops / deploy
- [ ] `Dockerfile` — multi-stage, non-root, distroless-ish
- [ ] `docker-compose.yml` — app + postgres + redis for local dev
- [ ] `.dockerignore`
- [ ] `.github/workflows/ci.yml` — lint (ruff), typecheck (mypy), test (pytest), build
- [ ] `k8s/deployment.yaml`, `k8s/service.yaml`, `k8s/hpa.yaml`, `k8s/networkpolicy.yaml`
- [ ] `pyproject.toml` — tool configs (ruff, mypy, pytest)
- [ ] `requirements-prod.txt` — pinned versions
- [ ] `requirements-dev.txt`

### Tests
- [ ] `tests/conftest.py` — fixtures for DB, tenant context, test users
- [ ] `tests/test_auth.py` — login, lockout, MFA, refresh rotation, theft detection
- [ ] `tests/test_tenancy.py` — cross-tenant leak prevention (both ORM filter + insert guard)
- [ ] `tests/test_audit.py` — chain integrity, tamper detection
- [ ] `tests/test_feedback.py` — aggregation math, training dataset build
- [ ] `tests/test_prioritization.py` — threshold resolution precedence, learned weight application

### Docs
- [ ] `MIGRATION_GUIDE.md` — file-by-file: how each demo module maps to enterprise equivalent
- [ ] `docs/ARCHITECTURE.md` — layer diagram, data flow, threat model
- [ ] `docs/SECURITY.md` — controls matrix (OWASP ASVS L2), incident response
- [ ] `docs/OPERATIONS.md` — runbook, alerts, SLOs, on-call
- [ ] `docs/DATA_GOVERNANCE.md` — retention, deletion (GDPR/CCPA), residency
- [ ] `README.md` — updated overview

## Design decisions made (so we don't re-litigate)

- **Postgres, not SQLite** in staging/prod (enforced by config validator)
- **Argon2id** for passwords (OWASP 2024 baseline params)
- **JWT access + opaque refresh** (access is stateless for perf; refresh is stateful so revocation works)
- **Refresh rotation with reuse detection** = revoke all sessions on suspected theft
- **TOTP for MFA** (compatible with any authenticator; WebAuthn/passkeys deferred to phase 2)
- **Row-level tenancy** via ORM filter + insert guard + optional Postgres RLS (defense in depth)
- **Tamper-evident audit** via per-tenant SHA-256 hash chain
- **Category thresholds** with (category, region) > (category) > (default) precedence
- **Learned weights** are BOUNDED multipliers [0.5, 1.5] applied on top of rules — not a full ML replacement, so recommendations stay explainable
- **Data source is a Protocol** with SQL / REST / CSV adapters; services depend on the protocol, not concrete class

## To resume

Say: "continue the enterprise hardening from the checkpoint" — I'll pick up at the API layer (which unblocks everything else since routes wire the services together).
