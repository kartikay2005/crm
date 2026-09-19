# Deployment Guide

Verified locally before writing this: clean `pip install`, `alembic upgrade
head`, full pytest suite (43/43), a real HTTP round trip (login → protected
route → dataset upload → analysis), a clean frontend build, and
`scripts/seed_demo.py` producing two real SHAP-explained predictions plus
one honest fallback — all confirmed against a running server, not assumed.
See the README's "Verified" section for the full list.

## Before you deploy anywhere

1. **Generate real secrets** — never ship `.env.example`'s dev values:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"          # JWT_SECRET_KEY
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FIELD_ENCRYPTION_KEY
   ```
2. **Don't ship SQLite.** `core/config.py` has a validator that refuses
   SQLite outside `ENV=local` — Postgres is required for staging/prod.
3. **Lower `DATASET_MAX_UPLOAD_MB`.** Upload runs synchronously in-request
   (no background queue yet — see `IMPLEMENTATION_PLAN.md` §10.2). Most
   free-tier platforms cap request time around 30–100s; drop the default
   `200` to `20`–`30` for a free web-service deployment.

## Step-by-step: Render (backend) + Neon (Postgres) — free

Render's free Postgres expires after 30 days; Neon's free tier is
permanent (0.5GB, no card required), so pairing them avoids a monthly
database wipe.

1. **Create the database.** Sign up at neon.tech, create a project, copy
   the connection string.
2. **Run migrations against it, once, from your machine:**
   ```bash
   DATABASE_URL="<your neon connection string>" python3 -m alembic upgrade head
   ```
3. **Seed the demo account and sample datasets, once, against the same URL:**
   ```bash
   DATABASE_URL="<your neon connection string>" python3 scripts/seed_demo.py
   ```
   This prints the login credentials (`demo` / `demo@example.com` /
   `RecruiterDemo2026!`) and pre-loads 3 analyzed datasets so the app isn't
   empty on first login. Also run
   `DATABASE_URL="..." python3 scripts/generate_demo_sellers.py` if you
   want the Sellers/Prioritization endpoints populated too.
4. **Push this repo to GitHub.** `.gitignore` already excludes `.env`,
   `venv/`, `node_modules/`, `*.db`, `__pycache__/` — check `git status`
   before your first commit to be sure none of those are staged.
5. **Create a Render Web Service** pointed at the repo — it auto-detects
   the root `Dockerfile`. Free tier: 750 instance-hours/month, spins down
   after 15 minutes idle (~1 minute cold start on the next request).
6. **Set environment variables on Render**, matching `.env.example`:
   `DATABASE_URL` (Neon's string), `JWT_SECRET_KEY`, `FIELD_ENCRYPTION_KEY`
   (your generated ones from step 1), `ENV=staging`, `REDIS_URL` left
   blank, `DATASET_MAX_UPLOAD_MB=30`.
7. **Populate the model registry at build time** (its `.joblib` files are
   `.gitignore`d — binaries don't belong in git): add
   `python3 scripts/train_demo_models.py` to Render's build command, after
   `pip install -r requirements.txt`.
8. **Deploy the frontend.** Render Static Sites are free and unlimited, or
   use Vercel.
   - Set `VITE_API_BASE_URL` to your Render backend's
     `https://<your-service>.onrender.com/api/v1`
   - Set `VITE_DEMO_MODE=true` — this shows a "Demo mode" hint box with a
     one-click "Fill demo credentials" button right on the login page, so
     a recruiter doesn't need to ask you for a password
   - `npm run build`, deploy the `frontend/dist/` output
9. **First real integration test.** This is the first time the Docker
   image actually builds and runs outside a dev machine (no Docker daemon
   was available while building this repo). Watch the build logs, then
   click through the deployed frontend yourself end to end — login (or
   use the demo-credentials button), the Datasets list, and one dataset's
   full analysis — before sending the link to anyone.

## Frontend on Vercel or GitHub Pages

The backend still needs Render (or another real server host) either way —
Vercel and GitHub Pages are both static hosts and can't run the FastAPI
app. These replace step 8 above.

### Vercel (recommended — zero-config for Vite, custom domains, previews)

1. Import the repo at vercel.com → **Project Settings → Root Directory**
   → set to `frontend` (this is a monorepo-style layout, not a
   repo-root frontend).
2. Vercel auto-detects the Vite framework preset (build command
   `npm run build`, output `dist`) — nothing else to configure.
   `frontend/vercel.json` is already in place with the SPA rewrite rule
   (every path serves `index.html`, so `react-router-dom`'s
   `BrowserRouter` can take over client-side — without it, refreshing on
   `/datasets/abc` would 404).
3. Add environment variables: `VITE_API_BASE_URL` =
   `https://<your-render-backend>.onrender.com/api/v1`, and
   `VITE_DEMO_MODE=true`.
4. Deploy. Every push to `main` auto-deploys; every PR gets a preview URL.

### GitHub Pages (also free, works well, one extra caveat)

GitHub Pages has no server-side rewrites (unlike Vercel), so an SPA needs
two adjustments the included `.github/workflows/deploy-pages.yml` already
handles for you:
- **Base path**: a GitHub Pages project site serves from
  `username.github.io/<repo-name>/`, not `/`, so assets need that prefix
  — the workflow passes `--base=/${{ github.event.repository.name }}/` to
  `vite build` automatically (verified locally: asset paths in the built
  `index.html` correctly carry the prefix).
- **Deep-link 404s**: GitHub Pages serves a real 404 for any path it
  doesn't recognize as a file — the workflow copies the built
  `index.html` to `404.html` so GitHub Pages serves the app itself
  instead, letting `BrowserRouter` render the right page client-side.

To use it:
1. Repo **Settings → Pages → Source → "GitHub Actions"**.
2. Repo **Settings → Secrets and variables → Actions → Variables → New
   repository variable**: `VITE_API_BASE_URL` =
   `https://<your-render-backend>.onrender.com/api/v1`.
3. Push to `main` (or run the workflow manually from the Actions tab) —
   it builds and deploys automatically. `VITE_DEMO_MODE` is already set
   to `true` in the workflow.

Both options serve the frontend for free, indefinitely, with no cold
start (static hosting doesn't sleep the way Render's free web service
does) — Vercel gets you a slightly smoother experience (previews, no base
path caveat) but either is genuinely fine to hand to a recruiter.

## Alternative: docker-compose, anywhere with a Docker host

If you have (or can rent) any VM with Docker — a $4-6/mo box, or a free
trial credit on any provider — `docker-compose.yml` already wires app +
Postgres + Redis with health-check-gated startup:

```bash
cp .env.example .env   # then fill in real secrets per step 1 above
export JWT_SECRET_KEY=... FIELD_ENCRYPTION_KEY=...
docker compose up --build
docker compose exec app python3 scripts/seed_demo.py
```

`docker-entrypoint.sh` runs `alembic upgrade head` automatically on
container start — idempotent, but per its own comment **not safe for
multi-replica deployments** (Kubernetes `replicas > 1`); use a dedicated
migration Job/initContainer there instead.

## What "production-ready" still requires beyond a free demo deploy

Being honest about the gap between "impresses a recruiter" and "ready for
real users/traffic," per `IMPLEMENTATION_PLAN.md` §10.4:

- Background task execution for uploads (currently synchronous)
- Load testing / production traffic simulation (not done)
- Real trained models in `model_registry/` — the shipped ones are trained
  on synthetic data for pipeline validation, not real predictions (see
  `docs/ARCHITECTURE.md` extension point 3)
- An always-on tier once cold starts or the 750-hour/month cap matter
