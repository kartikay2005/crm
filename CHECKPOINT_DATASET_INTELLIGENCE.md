# AI Dataset Intelligence Engine — Segment 1 Checkpoint

Saved: 2026-07-21. Extends the existing `crm-enterprise` project (see
`CHECKPOINT.md` for that history) rather than a separate codebase.

## Segment 1 — DONE: Foundation (validate → read → clean → profile)

- `services/dataset_intelligence/validation.py` — Step 1. Extension, size,
  empty-file, duplicate-header, encoding, delimiter, JSON-shape, and Excel
  magic-byte checks. Never raises for expected failures; returns a
  structured `ValidationResult` with friendly per-issue messages.
- `services/dataset_intelligence/readers.py` — unified CSV/XLSX/XLS/TSV/JSON
  → DataFrame reader, plus conservative, non-destructive cleaning (trim
  whitespace, dedupe headers, best-effort numeric/datetime coercion — never
  drops rows or imputes).
- `services/dataset_intelligence/profiler.py` — Step 2, full profile: shape,
  missingness, duplicates, per-column type inference (numerical/categorical/
  datetime/boolean/text/constant), target-column candidate detection,
  correlation matrix, skewness/kurtosis, IQR outlier %, class-imbalance
  signal. Samples at `dataset_profile_sample_rows` (default 200k) for
  expensive stats on very large files.
- `services/dataset_intelligence/pipeline.py` — orchestrates the four steps,
  persists `Dataset` + `DatasetProfileRecord`, tenant-scoped, audit-logged,
  every failure mode translated to a friendly `DatasetIntelligenceError`.
- `models/dataset_intelligence.py` — `Dataset`, `DatasetProfileRecord`
  (tenant-scoped, portable JSON column).
- `api/routes/dataset_intelligence.py` — `POST /datasets/upload`,
  `GET /datasets/{id}/profile`. Wired into `api/main.py`.
- `api/schemas.py` — `DatasetProfileOut`, `DatasetUploadResponse`, etc.
- `models/rbac.py` — added `Perm.DATASETS_READ` / `DATASETS_WRITE`, granted
  to `account_manager`, `team_lead`, `executive_viewer` (read-only),
  `tenant_admin`.
- `core/config.py` — `dataset_upload_dir`, `dataset_max_upload_mb`,
  `dataset_allowed_extensions`, `dataset_profile_sample_rows`.

**Verified working end-to-end**: real CSV through the full HTTP stack
(login → multipart upload → validate → read → clean → profile → persist →
re-fetch), bad-file rejection, unauthenticated-request rejection, all via
FastAPI's `TestClient` — not just direct service calls.

## Bugs found and fixed while building/testing this segment

Several were **pre-existing bugs in the CRM hardening work from the
previous session**, latent because that work was only ever tested via
direct service-layer calls, never through a real HTTP request. Building
Segment 1's route and testing it with `TestClient` surfaced them:

1. **Critical, cross-cutting**: `api/deps.get_tenant_context` was a *sync*
   generator dependency. FastAPI dispatches sync dependencies through a
   worker thread pool; `ContextVar.set()` calls made inside that pooled
   thread do not propagate back to the event-loop context that runs the
   rest of the request. Every request to every route using
   `get_tenant_context` (sellers, prioritization, feedback, admin, and now
   dataset intelligence) was hitting "No tenant context set" the moment it
   went through real HTTP dispatch. **Fixed** by making the dependency
   `async def` — async dependencies run directly on the event loop, so the
   `tenant_scope()` side effect is visible where it's needed. This is a
   single shared-dependency fix that resolves the issue for every route at
   once; verified via `TestClient` against dataset-intelligence, sellers,
   prioritization, and admin routes.
2. `core.tenancy.tenant_scope` used `ContextVar.Token.reset()` to restore
   the previous value on exit. `Token.reset()` requires the reset to happen
   in the *same* Context it was created in and raises otherwise — which is
   exactly what a thread-pool-dispatched sync generator dependency
   violates. **Fixed** by capturing and restoring the previous value via
   plain `ContextVar.set()` instead, which has no such restriction. (Same
   fix applied to the `unscoped_bootstrap_query` bypass for consistency.)
3. `core.tenancy.TenantScopedMixin` was a bare marker class; `with_loader_
   criteria()` needs the root class it's given to have a *real* mapped
   attribute for its lambda-analysis/caching step, not just a flag.
   **Fixed** by merging the marker and the actual `tenant_id` mapped column
   onto one class.
4. The tenant-filter lambda closed over `ctx.tenant_id` (attribute access
   on a captured dataclass instance); SQLAlchemy's lambda-caching layer
   requires closure variables to be plain literal/bind values. **Fixed** by
   extracting `tenant_id_for_filter = ctx.tenant_id` as a local `str`
   before the lambda.
5. `Session.get()` (identity-map lookup) bypasses the tenant query filter
   entirely if the row was already loaded under a *different* tenant_scope
   earlier in the same Session — identity-map hits never issue SQL, so
   there's no query for the filter to attach to. Not exploitable via the
   normal API path (one fresh Session per request), but a real trap for
   any future multi-tenant batch/background job. **Fixed** by adding
   `core.database.session_scope_for_tenant()` as the required pattern for
   that use case, with a loud docstring explaining why, plus a regression
   test in `scripts/smoke_test.py` that asserts the unsafe pattern actually
   leaks (so a future SQLAlchemy version silently "fixing" this would be
   caught).
6. `models.audit.AuditLog`'s hash-chain ordering used `(occurred_at, id)`
   as the tie-breaker; `id` is a random UUID, so two events written in the
   same timestamp tick could replay in the wrong order relative to how the
   chain was actually built, breaking `verify_chain()`. **Fixed** by adding
   a true autoincrementing `seq` column as the ordering key.
7. Numpy scalar leakage into JSON columns: `np.float64`/`np.bool_` values
   from pandas aggregations (`.iloc[]`, `.sum()`, comparisons) aren't
   JSON-serializable and broke the DB insert. **Fixed** by explicit
   `float()`/`bool()`/`int()` casts at every point a numpy scalar could
   reach a `PortableJSON` column (`services/dataset_intelligence/
   profiler.py`).
8. Missing runtime deps caught by actually running the app:
   `email-validator` (Pydantic `EmailStr`), `python-multipart` (FastAPI
   file uploads).
9. RBAC default-role gap: `account_manager` (the primary daily-use persona)
   had no `datasets:read`/`datasets:write` — caught because the HTTP test
   used real permission enforcement, not a mocked/bypassed check.

## Segment 2 — DONE: Domain Detection + Confidence + Quality + Readiness Scores

- `services/dataset_intelligence/domain_knowledge.py` — data-only knowledge
  base for all 20 spec domains (signature columns, keyword vocab, curated
  known-good numeric ranges, expected numerical/categorical mix). Adding a
  21st domain or tuning an existing one is a data change here, never a
  change to the scoring logic.
- `services/dataset_intelligence/domain_detection.py` — Steps 3-4. Six
  weighted components exactly per the spec (column similarity 35%, feature
  names 20%, data types 15%, distribution similarity 10%, schema
  completeness 10%, domain keywords 10%). Returns every sub-score and the
  matched columns per domain, not just a final number — explainable by
  construction. Falls back to "General Tabular Dataset" when the best
  domain scores below 35%, rather than force-fitting a misleading label.
  Green/yellow/red banding at 80/50 thresholds.
- `services/dataset_intelligence/quality_readiness.py` — Steps 5-6. Quality
  score aggregates the 8 signals the spec lists (missing values, outliers,
  duplicates, feature completeness, null %, datatype consistency, class
  imbalance) purely from data already in the DatasetProfile — no rescanning.
  AI Readiness score combines quality, missingness, domain confidence,
  schema match, training-set size adequacy, and target availability.
  **Known simplification**: "schema match" is currently proxied by the
  domain's generic schema_completeness sub-score, since there's no real
  per-model schema to match against yet — Segment 3's model registry should
  replace this proxy with a genuine schema-match score once it exists.
- `models/dataset_intelligence.py` — added `DatasetAnalysisRecord` (domain
  scores, quality breakdown, readiness breakdown, all as portable JSON).
- `services/dataset_intelligence/pipeline.py` — `ingest_and_profile` renamed
  to `ingest_and_analyze`, now runs all six steps and returns an
  `AnalysisResult` bundle. Domain/scoring failure degrades gracefully
  (dataset stays at `status="profiled"` with the profile still usable,
  rather than losing the whole upload over a scoring bug).
- `api/routes/dataset_intelligence.py` — upload response now includes
  `top_domain`, `domain_confidence`, `domain_band`, full `domain_scores`
  list, `quality`, and `readiness`. New `GET /datasets/{id}/analysis`
  endpoint to re-fetch without re-uploading.

**Verified working end-to-end**: two real, genuinely different datasets
(healthcare-shaped vs. finance-shaped CSVs) through the full HTTP stack —
domain detection correctly identified each with a wide margin over the
next-best domain (Healthcare 75.5% vs. 25.6%; Finance 73.4% vs. 19.8%),
quality/readiness scores were sensible (both flagged the small row counts
as a readiness concern, as they should), and the analysis re-fetch endpoint
returned matching data. Full response round-trips through JSON cleanly.

### Bug found this segment
`_distribution_similarity_score` in `domain_detection.py` accumulated a
`pandas.Series.mean()` result (numpy float64) without casting, leaking
`np.float64(...)` into logs and the persisted analysis record. Not a hard
crash this time — unlike Segment 1's `np.bool_` leak, `np.float64` actually
*subclasses* Python's `float`, so JSON serialization tolerated it — but it's
sloppy and inconsistent with the numpy-scalar discipline established in
Segment 1. Fixed with explicit `float()` casts at both the per-field and
accumulator level.

## Segment 3 — DONE: Model Registry, Schema Matching, Model Selection, Fallback Logic

- `model_registry/` — folder-based registry per spec Step 8/26: `<domain>/
  <model_name>.joblib` + sidecar `<model_name>.json` metadata. Adding a new
  domain or model is a filesystem drop, never a code change.
  Populated by `scripts/train_demo_models.py` with two **genuinely
  trained** LogisticRegression models (Heart Disease Risk Predictor,
  accuracy 0.825; Loan Default Predictor, accuracy 0.762 — both on
  synthetic-but-realistic data, generated and trained fresh, not
  hand-faked numbers) plus one metadata-only stub (Retail Revenue
  Forecaster) specifically to exercise the "schema matches but artifact
  unavailable" fallback path.
- `services/dataset_intelligence/model_registry.py` — metadata schema
  (`ModelMetadata`, `FeatureSpec`), filesystem scanner that skips and logs
  malformed entries rather than crashing the whole registry, `lru_cache`d
  accessor so it's not rescanned every request.
- `services/dataset_intelligence/schema_matching.py` — Step 7. Weighted
  match: required-feature coverage (60%), dtype compatibility (20%),
  missing-data compliance (10%), optional-feature bonus (10%). Fuzzy
  column-name matching reused from the domain detector's normalization
  approach.
- `services/dataset_intelligence/model_selection.py` — Steps 9-10. Hard
  80% threshold per spec Step 10 ("never force predictions") — `selected`
  is `None` below it, full stop, with a specific plain-English reason for
  each of three distinct fallback causes: (a) domain itself didn't match
  anything (General Tabular Dataset), (b) no models registered for the
  detected domain, (c) best schema match below threshold, (d) schema
  matches well but the trained artifact isn't on disk yet.
- `models/dataset_intelligence.py` — `DatasetAnalysisRecord` extended with
  `selected_model_name/version`, `schema_match_pct`, `fallback_reason`,
  `considered_models_json`.
- `services/dataset_intelligence/quality_readiness.py` — **closed
  Segment 2's documented gap**: `compute_readiness_score` now accepts a
  real `schema_match_pct` from actual model selection and uses it instead
  of the domain-completeness proxy whenever selection was attempted (even
  if it landed below threshold — "we checked and got 55%" is a better
  signal than "we didn't check"). Verified this actually changed the
  readiness numbers: 76.6 → 82.6 (Healthcare), 77.1 → 82.7 (Finance).
- `api/schemas.py` / `api/routes/dataset_intelligence.py` — upload response
  and the `/analysis` endpoint both now include the full model-selection
  outcome (selected model + version + match %, or the fallback reason,
  plus every candidate considered with why it did/didn't match).

**Verified working end-to-end**, all via real execution against real data:
1. Healthcare CSV → correctly selected Heart Disease Risk Predictor at
   100% schema match.
2. Finance CSV → correctly selected Loan Default Predictor at 100% match.
3. Synthetic retail-shaped data → correctly matched Retail Revenue
   Forecaster's schema at 100% but refused to select it (artifact not on
   disk), with the exact "matches but not available yet" message.
4. Deliberately incomplete healthcare-shaped data (1 of 4 required columns
   present) → correctly refused to select any model (55% < 80% threshold),
   naming the exact missing columns. This is the spec's core "never force
   predictions" requirement, and it's enforced as a hard threshold in code,
   not just a suggestion in a docstring.
5. Full response round-trips through JSON cleanly; re-fetching via
   `GET /datasets/{id}/analysis` returns matching data.

## Segment 4 — DONE: Prediction Engine + Explainable AI (SHAP)

- `services/dataset_intelligence/prediction.py` — Step 11. Loads the real
  `.joblib` artifact selected by Segment 3, builds a feature matrix via the
  same fuzzy column-matching used by schema matching, imputes missing
  values (median/mode) rather than silently dropping rows, runs real
  `estimator.predict()` / `predict_proba()`. Returns prediction, per-class
  probabilities, confidence, model used, inference time, and throughput —
  every field the spec's Step 11 asks for. Explicitly scoped to
  binary/multiclass classification and regression (anything exposing the
  standard sklearn interface); forecasting/clustering/anomaly detection
  raise a clear "not yet supported" error rather than silently producing
  meaningless output from a generic `.predict()` call.
- `services/dataset_intelligence/explainability.py` — Step 12. Uses the
  **real `shap` library**, not an approximation: `shap.LinearExplainer`
  (exact, fast) for linear models, `shap.Explainer` (auto-selecting) for
  anything else. Produces both global feature importance (mean |SHAP
  value|) and per-row local explanations with natural-language summaries,
  matching the spec's example format.
- `scripts/train_demo_models.py` — extended to keep the Finance model's
  training features aligned with its declared schema (see bug below).
- `models/dataset_intelligence.py` — new `DatasetPredictionRecord` table,
  kept separate from `DatasetAnalysisRecord` to avoid bloating it with
  row-level prediction/SHAP payloads.
- `services/dataset_intelligence/pipeline.py` — prediction + explanation
  now run automatically after model selection, but **only when a model was
  actually selected** — this is enforced as a second, independent check
  (`if selection.selected_model is not None`), not just inherited from
  Segment 3's logic. Failures degrade gracefully (dataset stays at
  `status="analyzed"`, profile/domain/quality/readiness all still usable).
- `api/schemas.py` / `api/routes/dataset_intelligence.py` — upload response
  gets a lightweight `prediction_summary` (model, distribution, top 5
  global features); full row-level predictions and local explanations live
  behind a new `GET /datasets/{id}/predictions`, which correctly 404s with
  a helpful pointer to `/analysis` when the fallback path was taken.

**Verified working end-to-end**, all via real execution:
1. Healthcare dataset → real predictions (11 positive / 9 negative out of
   20 rows), real SHAP values whose global importance ranking (cholesterol
   > age > blood_pressure > heart_rate) **correctly matches the actual
   weights used to generate the synthetic training labels** — this is a
   meaningful correctness check, not just "it didn't crash."
2. Finance dataset → same, credit_score dominates as expected.
3. Fallback dataset (incomplete schema) → `prediction_summary` is `None`
   in the upload response, and `GET /datasets/{id}/predictions` correctly
   returns 404 rather than an empty/fake result. "Never force predictions"
   holds at this layer too, independently re-verified, not just trusted
   from Segment 3.

### Bug found and fixed this segment
The Loan Default Predictor was trained on 5 features (including
`interest_rate`) but its registry metadata only declared 4 as
`required_features` (`interest_rate` was `optional`) — so at inference time
`_build_feature_matrix` built a 4-column matrix while the fitted model
expected 5, and sklearn raised `ValueError: X has 4 features, but
LogisticRegression is expecting 5 features`. This is exactly the kind of
registry/model mismatch a real deployment could hit when someone edits
metadata without retraining (or vice versa). Fixed by retraining the model
on exactly its 4 declared required features, and **added a proactive
pre-flight check** in `prediction.py` (`estimator.n_features_in_` vs
`X.shape[1]`) that raises a specific, actionable error naming the exact
mismatch — so a future version of this bug fails with a clear diagnosis
instead of a generic sklearn traceback.

## Segment 5 — DONE: Insight Generator + Recommendation Engine

- `services/dataset_intelligence/insight_generator.py` — Step 13.
  Deterministic, rule-based generators (no black-box summarization needed
  to be correct): correlation insights (`|r| >= 0.7`), outlier insights
  (`outlier_pct > 15%`), class-imbalance insights (reuses Segment 2's
  signal), quality-note insights (surfaces Segment 2's quality notes
  directly), trend insights (linear-slope-over-time, only emitted when a
  datetime column exists and the relative slope is meaningful — never
  forces a trend narrative onto static data), and prediction-summary
  insights. Optional LLM polish is additive-only: template text is always
  correct first, LLM rewrite is attempted only if
  `enable_llm_features`+API key are set, with a hard timeout and a
  try/except that silently falls back to the template on any failure —
  insight *content* never depends on the LLM being reachable.
- `services/dataset_intelligence/recommendation_engine.py` — Step 16.
  Per-domain rule registry (`_DOMAIN_RULES: dict[str, list[RuleFn]]`) —
  adding a new domain's recommendations is "append a function to a list."
  Implemented real rules for Finance (debt ratio, prediction-risk %),
  Healthcare (BMI, prediction-risk %), Retail (low inventory heuristic),
  plus starter rules for Marketing and HR. A domain with no rules returns
  `[]`, not an error — same fallback-friendly pattern as everywhere else.
- **New migration**: `alembic/versions/002_dataset_intelligence.py` —
  covers ALL FIVE dataset-intelligence tables (`datasets`,
  `dataset_profiles`, `dataset_analyses`, `dataset_predictions`,
  `dataset_insights`), because none of them had ever actually been
  migrated before now — Segments 1-4's testing all used
  `Base.metadata.create_all()` directly against SQLite as a dev shortcut,
  and this gap wasn't caught until writing Segment 5's own migration
  per the implementation plan. Actually ran `alembic upgrade head` against
  a throwaway SQLite DB (not just eyeballed the SQL) — confirmed both
  migrations apply cleanly and produce all 16 expected tables.
- `models/dataset_intelligence.py` — `DatasetInsightRecord` (unlike
  `DatasetPredictionRecord`, this is created unconditionally — insights
  run on the fallback path too, per the spec's own fallback-message
  promise of "exploratory insights are shown").
- `services/dataset_intelligence/pipeline.py` — insights/recommendations
  now run after the prediction block, **not gated behind
  `selection.selected_model`** (the one deliberate asymmetry vs. Segment
  4's prediction step).
- `api/schemas.py` / `api/routes/dataset_intelligence.py` — lightweight
  `insights_summary` in the upload response; full detail behind
  `GET /datasets/{id}/insights` and `GET /datasets/{id}/recommendations`.

**Verified working end-to-end**, all via real execution:
1. Both real test datasets produce correct, non-generic insights (strong
   correlations correctly identified, e.g. `age`↔`cholesterol` r=0.99 in
   the healthcare data) and domain-appropriate recommendations.
2. Fallback dataset (the same incomplete-schema one from Segment 4) still
   gets a full insight bundle and a 200 from `/insights` — confirmed
   `predicted: false` but `insights_generated: true` in the same pipeline
   run, which is exactly the asymmetry the spec calls for.
3. Full JSON round-trip clean on both the summary and detail responses.

### Two real bugs found and fixed this segment

**Bug 1 (caught by testing, not inspection)**: the Finance debt-ratio
recommendation fired with the rationale *"Average debt ratio in this
dataset is 51266.67"* — an impossible value for a ratio. Root cause: the
column-matching helper used **bidirectional** substring containment
(`candidate in column OR column in candidate`). The short real column name
`"income"` is a literal substring of the longer alias candidate
`"debt to income"`, so the reverse direction matched `"income"` when
searching for the debt-ratio column, and the rule silently computed its
threshold check against the wrong data entirely.

**This turned out to be a systemic bug, not a one-off**: the same
bidirectional pattern was present in four other places —
`domain_detection.py` (three functions), `schema_matching.py`, and
`prediction.py`. I constructed a concrete cross-domain failure case to
confirm the risk was real, not theoretical: the Healthcare keyword `"age"`
is a contiguous substring of the word `"average"`, so a Retail dataset
with an `average_revenue` column would have falsely inflated Healthcare's
domain-similarity score. **Fixed all five locations** to one-directional
matching (canonical term must appear *within* the actual column name,
never the reverse) and re-ran the full domain-detection + model-selection
+ prediction test suite afterward — confirmed zero regression (Healthcare
75.5%, Finance 73.4%, both scores byte-for-byte identical to before the
fix) while the debt-ratio recommendation now correctly evaluates against
the real `debt_ratio` column and correctly does *not* fire (real average
~0.43, a healthy value) — the fix didn't just stop an error, it corrected
an actual wrong business conclusion.

## Segment 6 — DONE: Visualization Generation

- `services/dataset_intelligence/visualization.py` — Step 14. Charts are
  computed **on demand**, never persisted (rebuilding is cheap; storing
  every chart's data would bloat the DB for no benefit — a deliberate
  design decision, not an oversight). One generic envelope
  (`{chart_id, type, title, data}`) so Segment 9's frontend needs one
  rendering component per `type`, not one per chart. Implemented: missing
  value matrix, correlation heatmap (reuses Segment 2's matrix, zero
  recomputation), per-column histograms/bar charts, outlier box-plot
  stats, SHAP feature importance (reuses Segment 4's output), prediction
  distribution, and — only when the uploaded dataset genuinely contains
  the model's real target column (a labeled validation set, not just
  inference input) — a real confusion matrix and ROC curve computed with
  `sklearn.metrics`. Builder functions take plain dicts/primitives rather
  than requiring live dataclass instances, since the API route only has
  DB-persisted JSON available, not the original in-memory Segment 1-4
  objects — this was a deliberate design correction made while wiring the
  route, not an afterthought.
- `api/routes/dataset_intelligence.py` — `GET /datasets/{id}/charts`.
  Re-reads and re-cleans the stored raw file rather than reconstructing a
  `DatasetProfile` from persisted JSON (cheaper than maintaining a
  JSON-to-dataclass deserializer, and guaranteed consistent with the
  actual file).

**Verified working end-to-end**, all via real execution:
1. Full healthcare dataset → 12 charts, including a genuinely computed
   confusion matrix and ROC curve (AUC correctly validated at both extremes
   — confirmed ~0.5 for a non-discriminating fake model and ~1.0 for a
   confident, correct one, proving the ROC math itself is right, not just
   "a number came out").
2. Fallback dataset (no model selected) → still gets 5 profile-level
   charts (missing matrix, correlation, 2 histograms, box plot) with no
   prediction-dependent charts — correct behavior, verified via real HTTP
   request, not assumed.
3. Full JSON round-trip clean.

### Two real bugs found and fixed this segment

**Bug 1**: an early version filtered "ID-like" numerical columns from
getting histograms using cardinality ratio alone
(`unique_count/n_rows >= 0.95`). This is unreliable at small sample sizes:
in the 20-row healthcare test dataset, legitimate continuous measurements
(`age`, `blood_pressure`, `cholesterol`) are *also* close to 100% unique
purely because real-valued data rarely repeats exactly in a small sample —
so the filter silently dropped histograms for every numerical column, not
just the actual `patient_id` column it was meant to catch. **Fixed** by
requiring both signals together: high cardinality *and* a name pattern
suggesting an identifier (`ends with "id"`). Verified the corrected version
keeps `age`/`blood_pressure`/`cholesterol` while still correctly dropping
`patient_id`.

**Bug 2 (a testing methodology catch, not a code bug)**: my first
confusion-matrix/ROC test fed in a *constant* fake probability for every
row regardless of the true label, which produced AUC=0.5 — technically
"not crashing," but not actually validating the ROC computation, since a
constant score can't discriminate between classes by construction. Caught
this before treating the result as a pass, rebuilt the test with
realistic discriminating probabilities, and confirmed AUC correctly hits
~1.0 for a genuinely accurate model. Worth recording as a reminder that a
plausible-looking passing number isn't the same as a validated one.

## Segment 7 — DONE: Report Export (PDF/Excel/CSV/JSON)

- `services/dataset_intelligence/report_generator.py` — Step 17.
  `assemble_report_data` is the single source of truth every format renders
  from, so PDF/Excel/CSV/JSON can't silently drift from each other. Uses
  real libraries throughout: `reportlab` for a genuine 1-2 page executive
  PDF (summary table, prediction section, top insights, recommendations —
  not a full profile dump), `openpyxl` for a real 4-sheet workbook
  (Summary, Column Profile, Predictions, Insights & Recommendations),
  stdlib `csv` for row-level prediction export, stdlib `json` for the full
  dump. CSV **raises a typed error** rather than returning an empty file
  when no prediction exists — a CSV of nothing is confusing, not helpful,
  per the implementation plan's own guidance.
- `api/routes/dataset_intelligence.py` — `GET /datasets/{id}/report?format=pdf|excel|csv|json`,
  correct `Content-Type` and `Content-Disposition: attachment` headers per
  format, 400 with a clear message when CSV is requested for a fallback
  dataset.

**Verified working end-to-end, and verified *correctly* — every format was
actually opened and parsed, not just checked for "bytes came back"**:
1. PDF: loaded with `pypdf.PdfReader`, confirmed it actually parses as a
   valid PDF and its extracted text contains the expected domain name.
2. Excel: loaded with `openpyxl.load_workbook`, confirmed all 4 sheets
   exist with the expected header structure.
3. CSV: parsed with `csv.reader`, confirmed correct row count and headers.
4. JSON: parsed with `json.loads`, confirmed all expected top-level keys
   and correct nested values.
5. Fallback dataset: PDF/Excel/JSON all still generate a sensible report
   (with the fallback reason surfaced instead of prediction data); CSV
   correctly raises `ReportGenerationError` instead of an empty file.
6. Full HTTP-layer test across all four formats plus an invalid `format`
   value (correctly 422s).

### One polish fix caught during testing
The initial download filename was `patients.csv_report.pdf` — the original
file's `.csv` extension was baked directly into the report filename rather
than being stripped first. Fixed to produce `patients_report.pdf`. Minor,
but the kind of rough edge that's easy to miss without actually inspecting
the response headers rather than just checking the status code.

## Segment 8 — DONE: Remaining REST Endpoints

**Before any new work**: found and fixed a **critical, previously-shipped
regression** while reviewing the route file — `GET /datasets/{id}/analysis`
had **no `@router.get` decorator at all**. Some earlier segment's
`str_replace` edit (inserting a new endpoint before it) accidentally
consumed the decorator line, leaving `get_dataset_analysis` as a dangling,
unregistered Python function. This endpoint had been silently unreachable
since sometime around Segment 4-6 — it was tested and confirmed working in
Segment 3, but never re-tested after that, so the regression went
undetected for multiple segments. Fixed by restoring the decorator, then
**verified via the actual OpenAPI schema** (not just a compile check) that
all 8 routes are genuinely registered, and via a real HTTP `TestClient`
call that `/analysis` returns 200. This is exactly the failure mode that
motivates Segment 10's planned formal pytest suite — a manual "did this
work last time I checked" testing style has a real blind spot for regressions
introduced by edits to unrelated-seeming parts of the same file.

- **New migration**: `alembic/versions/003_dataset_soft_delete.py` — adds
  `deleted_at` to `datasets`. Actually ran the full 3-migration chain
  against a throwaway SQLite DB and confirmed the column exists.
- `services/dataset_intelligence/model_registry.py` — added
  `reload_registry()`, distinct from the existing `get_cached_registry()`:
  bypasses the `lru_cache` so `POST /datasets/{id}/predict` can pick up a
  model added to the registry after the original upload, which is the
  entire point of that endpoint.
- `services/dataset_intelligence/pipeline.py` — `repredict_dataset()`.
  Re-reads and re-cleans the stored file, reuses the already-persisted
  domain (content hasn't changed, so domain hasn't either), runs
  `select_model()` fresh against the reloaded registry — **goes through
  the exact same fallback threshold as the original upload**, so this is
  not a backdoor to force a prediction that was correctly refused.
  Upserts `DatasetPredictionRecord`, updates `DatasetAnalysisRecord`'s
  model-selection fields in place.
- `api/routes/dataset_intelligence.py` — added:
  - `GET /datasets` — paginated list, proper `COUNT` query (not
    load-everything-then-`len()`), tenant-scoped by the existing ORM filter.
  - `POST /datasets/{id}/predict` — wraps `repredict_dataset`.
  - `DELETE /datasets/{id}` — soft delete via `deleted_at`, **not** a hard
    delete (a hard delete of a dataset with an audit trail pointing at it
    would be a data-integrity gap).
  - `GET /models`, `GET /models/domains` — new `models_router`, registered
    separately in `api/main.py` since these live outside the `/datasets`
    prefix.
  - Added `_get_active_dataset()` helper (soft-delete-aware) and applied
    it consistently across **all 7** existing single-dataset routes via a
    scripted, mechanical find-and-replace — not touched by hand one at a
    time, which would have risked exactly the kind of accidental-deletion
    mistake that caused the `/analysis` regression above.

**Verified working end-to-end — this segment's test is the most thorough
of the whole project so far**, per the implementation plan's explicit
instruction that cross-tenant isolation is the single most important test
in this segment:
1. **All 9 single-dataset endpoints** (`profile`, `analysis`,
   `predictions`, `insights`, `recommendations`, `charts`, `report`,
   `predict`, `delete`) tested against a second tenant attempting to
   access the first tenant's dataset — **every one correctly returns 404**,
   not 403 (403 would leak that the resource exists; 404 doesn't).
2. Dataset owner still has full access to their own data (200) after the
   cross-tenant check.
3. `GET /datasets` list correctly scoped per tenant (1 vs. 0).
4. `GET /models` returns all 3 registry entries (2 real, 1 stub) with
   correct `artifact_available` flags.
5. `GET /models/domains` returns all 20 domains.
6. `POST /datasets/{id}/predict` correctly re-selects the same model
   (nothing in the registry changed) — confirming it doesn't force a
   different result, just re-checks.
7. `DELETE` returns 204, and immediately makes the dataset return 404
   everywhere (not just hidden from the list) and drops it from
   `GET /datasets`'s count — confirming the soft-delete check is applied
   consistently, not just in the list endpoint.

## Segment 9 — DONE: Frontend Dashboard (React)

- **Stack adaptation**: the plan assumed Tailwind v3's `npx tailwindcss init -p` workflow; npm installed v4 by default, which changed configuration entirely (CSS-first `@theme`/`@custom-variant` blocks instead of `tailwind.config.js` content globs, `@tailwindcss/postcss` instead of the old plugin). Adapted on the spot rather than pinning to an older version, since v4 is npm's current default and pinning would just defer the same migration.
- **Deliberate design system**, not a generic AI-SaaS default: an "instrument panel" concept (ink-navy shell, warm paper canvas, muted teal/amber/crimson signal colors reading like indicator lights, Fraunces/Inter/IBM Plex Mono type stack) — chosen specifically to avoid the three AI-generated-design defaults the frontend-design skill calls out (cream+terracotta, near-black+acid-green, hairline-newspaper broadsheet). The signature element is a radial confidence-gauge styled like an analog dial, reused consistently for domain confidence, quality score, AI readiness, and schema match everywhere a 0-100 score appears — one visual through-line across the whole app rather than a flat progress bar repeated with no identity.
- Full API client layer (`src/api/types.ts` mirroring the backend Pydantic schemas, `src/api/client.ts` with JWT auth + silent refresh-on-401, `src/api/datasets.ts` with typed functions for all 13 backend endpoints).
- `AuthContext` (login, MFA challenge, logout) + `ProtectedRoute` guard.
- All planned components: `Upload` (drag-and-drop with client-side pre-validation), `DatasetPreview`, `ConfidenceGauge` (signature element), `DomainBadge`, `PredictionCard`, `InsightCard`/`RecommendationCard`, `Charts` (one recharts-backed renderer per backend chart `type`, plus a hand-rolled grid for `heatmap`/`box` since recharts has no native primitive for those), `ReportDownload`.
- 3 pages (`Login`, `DatasetList`, `DatasetDetail` with the full 6-tab layout: Profile/Domain/Prediction/Insights/Charts/Report) and routing.
- `useTheme` hook + `ThemeToggle` component for the spec's "dark mode compatible" requirement (Step 18).

**Verified working, with real checks — not just "it builds"**:
1. `npm run build` succeeds cleanly: TypeScript compiles with zero errors, every import resolves, Tailwind v4 genuinely processed the utility classes (20KB+ of real compiled CSS, not a pass-through).
2. `npm run lint` (the project's configured `oxlint`, not a generic ESLint assumption) reports zero errors.
3. Confirmed the `dist/index.html` output references assets that actually exist in `dist/assets/` — internally consistent build output.
4. **Confirmed dark mode is genuinely functional, not just assumed**: grepped the compiled CSS for the `:where(.dark, .dark *)` selector pattern generated by the `@custom-variant dark` rule, and confirmed it's present — meaning the CSS mechanism the `useTheme` hook's `.dark` class toggle depends on is real, compiled output, not a hoped-for behavior.

**One honest limitation**: could not verify actual browser rendering — this sandbox doesn't reliably support persistent background processes across tool calls, so a live `vite preview` + request check wasn't achievable here. The build/lint/compiled-CSS checks above are strong but not a substitute for opening it in a real browser; that should be the first thing done when this is picked up outside the sandbox.

### Three real bugs found and fixed during this segment (all before shipping, all caught by review/building, not by a user)

1. **MFA-detection logic bug**: `AuthContext`'s login function initially checked for a 202 (MFA-challenge) status inside a `catch` block — but 202 is a 2xx status, so axios resolves it as success and never enters `catch`. Fixed by checking the response status/shape directly in the success path.
2. **React fragment key bug**: `Charts.tsx`'s heatmap renderer used shorthand `<>...</>` fragment syntax with a `key` prop inside a `.map()` — React silently drops keys on shorthand fragments, which would have caused list-reconciliation bugs on re-render. Fixed with explicit `<Fragment key={...}>`.
3. **Rules of Hooks violation**: `DatasetDetail` had an early `return null` (for a missing `datasetId` param) placed *before* its `useQuery`/`useMutation` calls — hooks must run unconditionally in the same order every render. Fixed by moving the guard after all hook calls and using `enabled: !!datasetId` to gate the query instead.
4. **A real functional gap, not a bug**: every component was written with `dark:` Tailwind classes, but nothing in the app ever added the `.dark` class those rules depend on — dark mode CSS existed with no way to activate it. Caught while working through the plan's own "verify dark mode" checklist item rather than skipping it, and fixed with the `useTheme` hook + toggle button.

### One explicitly-flagged, unresolved gap (documented in code, not hidden)
`DatasetPreview.tsx` renders column-level profile statistics (types, missingness, top values), not literal row-by-row sample data, because the backend has no `GET /datasets/{id}/preview` endpoint returning raw rows — only the aggregate `DatasetProfile` from Segments 1-2. This is flagged explicitly in the component's own docstring rather than silently building against an endpoint that doesn't exist. Adding that endpoint is a Segment 8-shaped follow-up, not something to retrofit here.

## Segment 10 — DONE: Tests, Performance, CI, Postgres Integration Pass

### The headline result: real Postgres, for the first time in this project

Found a way to run genuine PostgreSQL in this sandbox with no Docker/apt
needed, via the `pgserver` pip package (an embedded Postgres binary). This
closed a gap that had existed since the very first CRM-hardening session,
where the RLS policies in `alembic/versions/001_initial_schema.py` had
been written and reasoned about but never actually executed against real
Postgres — only SQLite, where they're a documented no-op.

- Ran all 3 Alembic migrations against real Postgres 16.2 — clean, all 17
  tables, all 15 RLS policies created and enabled.
- **First attempt at testing RLS enforcement gave a false pass**: querying
  as the default `postgres` superuser showed both tenants' data, which
  looked like an RLS failure but wasn't — Postgres never applies row-level
  security to superusers, regardless of `FORCE ROW LEVEL SECURITY`. This
  is a genuine, previously-unverified operational requirement: **the app's
  runtime database role must not be a superuser**, or RLS provides zero
  protection no matter how correctly the policies are written. Rebuilt the
  test with a proper restricted `app_runtime_role` (SELECT/INSERT/UPDATE/
  DELETE only, no superuser) — matching what a real deployment must
  configure — and confirmed RLS then behaves exactly as designed: each
  tenant sees only its own rows, and no context set means zero rows,
  using nothing but raw SQL and a session variable, zero Python ORM
  involvement.
- Went further and ran the **entire application stack** (real HTTP
  requests via `TestClient`, real auth/login, the ORM-level tenant filter,
  AND Postgres RLS all active together) against this same real Postgres
  instance with the realistic non-superuser role — confirming both
  isolation layers coexist correctly, not just each in isolation.
- **Persisted this as `scripts/test_postgres_rls.py`**, not left as
  throwaway interactive commands — a real, re-runnable script with 8
  checks, documented rationale for why it's separate from the SQLite-based
  pytest suite, and confirmed working end-to-end (8/8 pass).

### Formal pytest suite (`tests/dataset_intelligence/`)

40 tests across 6 files: `test_validation.py`, `test_profiler.py`,
`test_domain_detection.py`, `test_model_selection.py`, `test_prediction.py`,
`test_tenancy_isolation.py`. Formalizes the manual verification from
Segments 2-8 into permanent, CI-enforced regression tests — including the
cross-tenant isolation check (parametrized across all 9 single-dataset
endpoints) and a routing-registration smoke test that walks the live
OpenAPI schema specifically to catch the class of bug that caused the
`/analysis` regression (a missing route decorator going undetected for
several segments).

**Building this test infrastructure surfaced two more real, previously-
invisible bugs**, both fixed:

1. **`core/database.py`**: `pool_size`/`max_overflow`/`pool_timeout` were
   passed unconditionally to `create_engine()`. These are QueuePool-
   specific arguments; SQLAlchemy auto-selects `SingletonThreadPool` for
   in-memory SQLite (`sqlite:///:memory:`), which doesn't accept them and
   raises a hard `TypeError` on engine construction. This was invisible
   through 8 prior segments of testing because every ad hoc test script
   used file-based SQLite, which happens to get `QueuePool` instead. Fixed
   by gating those three kwargs to Postgres only.
2. **Test fixture bug** (not app code, but worth recording): a naive
   `sqlite:///:memory:` fixture combined with FastAPI's thread-pool
   dispatch of sync dependencies meant the request-handling thread could
   see a completely different, empty in-memory database than the one the
   fixture populated — the SQLite equivalent of the ContextVar-across-
   threadpool bug documented in `api/deps.py`. Fixed with the standard
   `StaticPool` + `check_same_thread=False` pattern for testing in-memory
   SQLite in a multi-threaded app.

**And running the suite surfaced a third, more substantive bug** — a
second instance of the Segment 5 substring-matching bug family:

3. Segment 5's fix restricted matching to one direction (canonical term
   found within the actual column name, never the reverse) — correct for
   the bug it targeted, but a residual problem remained: raw substring
   containment doesn't respect word boundaries, so a short canonical term
   like `"age"` still matched inside an unrelated word like `"average"`
   even in the *correct* direction (`"age" in "average_revenue"` is
   trivially `True` as a character sequence). This exact matching logic
   had been independently duplicated across **four files**
   (`domain_detection.py`, `schema_matching.py`, `prediction.py`,
   `recommendation_engine.py`), which is precisely why the bug pattern
   could recur instead of being fixed once. **Fixed at the root**: created
   `services/dataset_intelligence/text_matching.py`, a single shared
   utility using word-boundary regex matching, and updated all four files
   to use it, deleting their local duplicated implementations. Re-ran the
   full domain-detection/model-selection/prediction pipeline against both
   real test datasets afterward and confirmed **zero regression** —
   identical domain confidence scores (Healthcare 75.5%, Finance 73.4%),
   identical model selections, identical predictions — while the
   originally-buggy Finance recommendation now correctly evaluates the
   real `debt_ratio` column instead of accidentally matching `income`.

### Performance

- **Chunked CSV/TSV reading**: files over 50MB now parse via
  `pd.read_csv(..., chunksize=50_000)` and concatenate, bounding peak
  memory during the read step. Verified chunked and non-chunked reads
  produce byte-identical output on the same data (not just "didn't
  crash") before considering this done.
- Documented, not implemented this segment (genuinely out of scope for a
  reasonable stopping point): true streaming profiling/prediction (a
  larger redesign), and background task execution for the upload endpoint
  (`FastAPI BackgroundTasks` or a real queue) — noted in
  `IMPLEMENTATION_PLAN.md` as the natural next step if upload latency
  becomes a real problem at scale.

### CI, Docker, and a gap that would have blocked everything

- **`requirements.txt` did not exist anywhere in this project until this
  segment.** Every package used across 10 segments (fastapi, SQLAlchemy,
  alembic, psycopg, argon2-cffi, cryptography, PyJWT, pyotp, structlog,
  prometheus-client, httpx, redis, pandas, numpy, scikit-learn, joblib,
  shap, reportlab, openpyxl, python-multipart, email-validator, uvicorn,
  gunicorn) had been installed ad hoc via `pip install` and never
  recorded. Built by cross-referencing every `import` statement in the
  codebase against installed versions — the CI workflow this segment also
  wrote (`pip install -r requirements.txt`) would have failed on its
  very first run without this. Also added `requirements-dev.txt` for
  test-only deps (`pytest`, `pgserver`).
- `.github/workflows/ci.yml` — backend job (installs deps, trains demo
  models, runs migrations against a real Postgres service container, runs
  the pytest suite, compile-checks every file) and frontend job (`npm ci`,
  lint, build). YAML syntax validated with a real parser, not eyeballed.
- `Dockerfile` — multi-stage, non-root runtime user, healthcheck,
  `gunicorn` + `uvicorn` workers for production (not dev-mode `--reload`).
- `docker-compose.yml` — app + postgres + redis, health-check-gated
  startup ordering, required secrets enforced via Compose's `:?` syntax
  rather than silently defaulting. YAML validated.
- `.dockerignore` added.

### Honest, explicitly-flagged limitation

Could not actually run `docker build`/`docker-compose up` in this sandbox —
no Docker daemon available (same constraint noted in Segment 9 for browser
testing). The Dockerfile and compose file are written correctly per
standard practice and the CI workflow's YAML is validated, but **actually
building and running the containers is the first thing to do when this
is picked up outside the sandbox** — flagged plainly, not glossed over.

## Segment status: all 10 segments complete

The AI Dataset Intelligence Engine, as specified, is now fully built:
upload → validate → read → clean → profile → detect domain → score
quality/readiness → match schema → select model (with real, enforced
fallback logic) → predict → explain (real SHAP) → generate insights →
recommend → visualize → export reports, plus a full REST API, a React
frontend, a formal test suite, and CI/Docker scaffolding — built on top of
the earlier CRM multi-tenant hardening work (auth, RBAC, audit logging,
now verified against real Postgres RLS).

## What's left, honestly

- Docker build/run never actually executed (no daemon in this sandbox)
- Frontend never rendered in an actual browser (no persistent background
  process support in this sandbox)
- True streaming profiling/prediction and background task execution for
  uploads, noted as future work in `IMPLEMENTATION_PLAN.md` but not built
- No load testing / production traffic simulation
- The demo ML models are trained on synthetic data for pipeline validation,
  not real-world accuracy — appropriate for this deliverable, explicitly
  not appropriate to deploy as-is against real healthcare/finance decisions
