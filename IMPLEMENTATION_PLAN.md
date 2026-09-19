# AI Dataset Intelligence Engine — Implementation Plan for Segments 5–10

This is a step-by-step build plan for the remaining work, written so it can
be followed directly in VS Code: exact file paths, what goes in each file,
terminal commands to run, and a definition of "done" for every segment.

**Prerequisite**: Segments 1–4 are complete (see `CHECKPOINT_DATASET_INTELLIGENCE.md`).
Every new file below assumes the existing `services/dataset_intelligence/`,
`models/dataset_intelligence.py`, `api/routes/dataset_intelligence.py`, and
`api/schemas.py` are in place and working.

**How to use this doc**: work top to bottom. Each segment ends with a
"Verify" checklist — don't move to the next segment until those pass. Run
everything from the `crm-enterprise/` root in an integrated VS Code
terminal with the project's virtualenv activated.

---

## Segment 5 — Insight Generator + Recommendation Engine (spec Steps 13, 16)

**Goal**: turn the profile/domain/quality/readiness/prediction data already
computed into plain-English findings and domain-aware recommendations.

### 5.1 — New service files

**Create `services/dataset_intelligence/insight_generator.py`**

- [ ] Define `@dataclass Insight`: `category: str` (one of `"trend"`,
      `"correlation"`, `"risk"`, `"anomaly"`, `"summary"`), `text: str`,
      `severity: str` (`"info" | "warning" | "critical"`), `supporting_data: dict`.
- [ ] Define `@dataclass InsightBundle`: `summary: str`, `key_findings: list[Insight]`,
      `risks: list[Insight]`, `hidden_trends: list[Insight]`, `anomalies: list[Insight]`.
- [ ] Implement `_correlation_insights(profile: DatasetProfile) -> list[Insight]`:
      iterate `profile.correlation_matrix`, flag pairs with `abs(corr) >= 0.7`
      (excluding self-correlation and duplicate pairs — only emit each pair once).
      Text template: `"{col_a} and {col_b} are strongly {positively/negatively}
      correlated (r={corr:.2f})."`
- [ ] Implement `_outlier_insights(profile: DatasetProfile) -> list[Insight]`:
      for each column with `outlier_pct > 15`, emit a `"anomaly"` severity
      `"warning"` insight naming the column and percentage.
- [ ] Implement `_imbalance_insights(profile: DatasetProfile) -> list[Insight]`:
      reuse `profile.class_imbalance` — if `is_imbalanced`, emit a `"risk"`
      insight naming the majority class percentage.
- [ ] Implement `_quality_insights(quality: ScoreBreakdown) -> list[Insight]`:
      surface `quality.notes` as `"risk"` category insights directly (they're
      already human-readable from Segment 2).
- [ ] Implement `_trend_insights(df: pd.DataFrame, profile: DatasetProfile) -> list[Insight]`:
      if any `datetime_columns` exist, pick the first one, sort by it, and for
      each numerical column compute a simple linear slope
      (`numpy.polyfit(x, y, 1)` where `x` is a numeric encoding of the date).
      Emit `"trend"` insight when `abs(slope)` is meaningfully large relative
      to the column's mean (e.g. `abs(slope * n_rows) > 0.1 * mean`). Skip
      entirely if no datetime column — don't force a trend narrative onto
      static data.
- [ ] Implement `_prediction_insights(prediction: PredictionResult | None) -> list[Insight]`:
      if a prediction was made, summarize the distribution in plain English
      (e.g. `"55% of rows were predicted high-risk for {target_variable}."`).
- [ ] Implement `generate_insights(df, profile, domain_result, quality, readiness,
      prediction) -> InsightBundle`: calls all the above, buckets results into
      `key_findings` / `risks` / `hidden_trends` / `anomalies` by category, and
      builds a 2–3 sentence `summary` string from the highest-severity items.
- [ ] **LLM enhancement (optional, feature-flagged)**: if
      `get_settings().enable_llm_features` and `openai_api_key` is set, add
      `_llm_polish(insights: list[Insight]) -> list[Insight]` that sends the
      template-generated insight texts to the OpenAI API asking it to rewrite
      them more naturally, with a **hard timeout** and **try/except that falls
      back to the original template text on any failure** — this must never
      block or break insight generation. Reuse the timeout/retry settings
      already in `core/config.py` (`llm_request_timeout_seconds`,
      `llm_max_retries`).

**Create `services/dataset_intelligence/recommendation_engine.py`**

- [ ] Define `@dataclass Recommendation`: `text: str`, `priority: str`
      (`"high" | "medium" | "low"`), `domain: str`, `rationale: str`.
- [ ] Build a `_DOMAIN_RULES: dict[str, list[Callable]]` registry, one rule
      function per domain, each with signature
      `(profile, prediction, quality) -> Recommendation | None`. Start with
      the domains that have real trained models (Healthcare, Finance) plus
      Retail (matches the spec's own examples):
      - Finance: if a numerical column matching `debt_ratio`/`debt ratio`
        exists and its mean > 0.6, or prediction distribution shows >30%
        high-risk, recommend *"Reduce lending to customers with Debt Ratio > 0.7."*
      - Healthcare: if a `bmi` column exists with values > 30 present,
        recommend monitoring; if prediction shows elevated risk %, recommend
        follow-up outreach.
      - Retail: if `quantity`/`inventory`-shaped columns show low values
        relative to historical average, recommend restocking (this is
        heuristic without real inventory history — document as
        best-effort in a comment).
      - Marketing, HR, etc.: stub with a single generic rule each (e.g.
        "review underperforming segments") — this is where the registry
        pattern earns its keep, since new rules are additive, not
        restructuring.
- [ ] Implement `generate_recommendations(domain, profile, prediction, quality)
      -> list[Recommendation]`: look up `_DOMAIN_RULES[domain]`, call each,
      collect non-`None` results, sort by priority. Return `[]` (not an
      error) for domains with no rules registered yet — this must degrade
      the same way model selection does, never crash.

### 5.2 — Persistence

**Edit `models/dataset_intelligence.py`** — add:

```python
class DatasetInsightRecord(Base, TenantMixin, TimestampMixin):
    __tablename__ = "dataset_insights"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True, unique=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    insights_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=list)
    recommendations_json: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=list)
```

- [ ] Add this class.
- [ ] Update `alembic/versions/001_initial_schema.py`'s `TENANT_SCOPED_TABLES`
      list — **actually, don't edit the already-applied migration**; instead
      create a new one: run
      `alembic revision -m "add dataset insights table"` and hand-write the
      `op.create_table(...)` for `dataset_insights` following the exact same
      pattern as `dataset_predictions` in the existing migration (columns,
      FK, indexes, RLS policy block). This is the correct pattern going
      forward — never edit a migration that could already be applied
      somewhere.

### 5.3 — Wire into the pipeline

**Edit `services/dataset_intelligence/pipeline.py`**:

- [ ] Import `generate_insights` and `generate_recommendations`.
- [ ] After the prediction/explainability block (which already handles the
      `selection.selected_model is None` fallback correctly), add a new
      **unconditional** step — insights and recommendations should run
      **even on the fallback path**, since exploratory insights are
      explicitly what the spec says to show when no model is selected
      (Step 10's fallback message literally promises this). Wrap in its own
      try/except that degrades gracefully like the others.
- [ ] Persist a `DatasetInsightRecord`.
- [ ] Add `insights: object = None` field to the `AnalysisResult` dataclass.
- [ ] Update the final `return` statement.

### 5.4 — API layer

**Edit `api/schemas.py`** — add `InsightOut`, `InsightBundleOut`,
`RecommendationOut`, and add an `insights_summary: InsightBundleOut` field
to `DatasetUploadResponse` (keep it lightweight — maybe just `summary` +
`key_findings` in the upload response, full detail behind a dedicated
endpoint, same pattern as predictions).

**Edit `api/routes/dataset_intelligence.py`**:

- [ ] Add `GET /datasets/{id}/insights` — full `InsightBundle`.
- [ ] Add `GET /datasets/{id}/recommendations` — full recommendation list.
- [ ] Both 404 cleanly if the record doesn't exist (shouldn't normally
      happen since this step is unconditional, but a dataset stuck at an
      earlier failed status is a real possible state).

### 5.5 — Verify

- [ ] `python3 -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('**/*.py', recursive=True)]"`
- [ ] Direct service-layer test: run `ingest_and_analyze` on
      `test_healthcare.csv` and `test_finance.csv`, print
      `analysis.insights.summary` and every recommendation — confirm the
      Finance recommendation about debt ratio actually fires on the finance
      test file (check its debt_ratio values first).
- [ ] Full HTTP test via `TestClient`: upload → check `insights_summary` in
      response → `GET /datasets/{id}/insights` → `GET /datasets/{id}/recommendations`.
- [ ] Test the fallback dataset (the partial-schema one from Segment 4) still
      gets insights (should NOT be `None`/404 — this is the one place
      fallback datasets *do* get real output).
- [ ] `json.dumps()` the full upload response — confirm no numpy-scalar
      leaks (this bug class has hit every prior segment; check for it
      proactively this time rather than waiting to find it).

---

## Segment 6 — Visualization Generation (spec Step 14)

**Goal**: produce chart *data/config*, not raster images — the backend
stays a pure API, rendering happens in Segment 9's frontend.

### 6.1 — Design decision (write this down before coding)

- [ ] Pick a chart-config shape the frontend can consume directly. Recommended:
      a generic envelope —
      `{"chart_id": str, "type": "bar"|"histogram"|"scatter"|"heatmap"|"pie"|"line", "title": str, "data": {...}}`
      where `data` matches what `recharts` (already an approved library per
      the frontend-design conventions used elsewhere in this project) expects
      for that chart type. Don't invent a bespoke schema per chart type
      beyond what's necessary — keep `data` shapes consistent (array of
      `{x, y}` or `{label, value}` objects) so the frontend can share one
      rendering component per chart `type`.

### 6.2 — New service file

**Create `services/dataset_intelligence/visualization.py`**

- [ ] `build_missing_value_matrix(df, profile, max_cols=30, max_rows=200) -> dict`
      — boolean grid (row × column, capped) of `is_missing`. For datasets
      larger than the cap, sample rows rather than truncate from the top
      (avoid a biased view).
- [ ] `build_correlation_heatmap(profile) -> dict` — reshape
      `profile.correlation_matrix` (already computed in Segment 2, no
      recomputation needed) into the heatmap `data` shape.
- [ ] `build_distribution_charts(profile) -> list[dict]` — one histogram
      per numerical column (bucketed counts — you'll need the raw df here
      since `DatasetProfile` doesn't store full distributions, only summary
      stats; compute with `numpy.histogram`), one bar chart per categorical
      column using `ColumnProfile.top_values` (already computed, no
      recomputation needed).
- [ ] `build_outlier_chart(df, profile) -> list[dict]` — box-plot summary
      stats (`min, q1, median, q3, max`) per numerical column via
      `df[col].describe()`.
- [ ] `build_feature_importance_chart(explainability_result) -> dict | None`
      — directly from `global_importance` (Segment 4 output), `None` if no
      prediction was made.
- [ ] `build_prediction_distribution_chart(prediction_result) -> dict | None`
      — directly from `prediction_distribution`.
- [ ] `build_confusion_matrix_and_roc(df, model, prediction_result) -> dict | None`
      — **only if the uploaded dataset actually contains the model's
      `target_variable` column** (i.e. it's a labeled validation set, not
      just inference input). Check for this explicitly; if absent, return
      `None` rather than fabricating metrics. When present: use
      `sklearn.metrics.confusion_matrix` and `roc_curve`/`auc` against the
      real predictions vs. real labels. This is a genuinely different,
      optional code path — document it clearly as "only when ground truth
      is present" so nobody mistakes it for evaluating on the dataset the
      model was trained on.
- [ ] `build_all_charts(df, profile, explainability_result, prediction_result,
      model) -> list[dict]` — orchestrator calling the above, skipping any
      that return `None`, capping total chart count (e.g. 15) if a very wide
      dataset would otherwise produce dozens of per-column histograms.

### 6.3 — Wiring

- [ ] Charts are **computed on demand**, not persisted — they're cheap to
      rebuild from already-stored profile/prediction data and storing every
      chart's data would bloat the DB for no benefit. Do NOT add a
      `DatasetChartRecord` table.
- [ ] **Edit `api/routes/dataset_intelligence.py`**: add
      `GET /datasets/{id}/charts`. Load the `Dataset`, `DatasetProfileRecord`,
      and `DatasetPredictionRecord` (if present) from DB, re-read the stored
      raw file from `dataset.storage_path` (needed for histogram/outlier/
      confusion-matrix charts that need raw values, not just profile
      summary stats), call `build_all_charts`.
- [ ] **Edit `api/schemas.py`**: add `ChartOut` (`chart_id`, `type`, `title`,
      `data: dict`).

### 6.4 — Verify

- [ ] Direct test: call `build_all_charts` on the healthcare test dataset's
      already-computed profile/prediction, print `[c["type"] for c in charts]`
      — confirm histogram, correlation heatmap, feature importance, and
      prediction distribution all appear.
- [ ] Full HTTP test: `GET /datasets/{id}/charts` for both an accepted-
      prediction dataset and a fallback dataset (fallback should still
      return profile-level charts like distributions/correlation, just no
      feature-importance/prediction-distribution ones).
- [ ] `json.dumps()` sanity check again.

---

## Segment 7 — Report Export (spec Step 17)

**Goal**: PDF / Excel / CSV / JSON export of everything computed so far.

### 7.1 — Dependencies

```bash
pip install reportlab openpyxl
```
(Add both to `requirements-prod.txt` once that file exists — see Segment 10.)

### 7.2 — New service file

**Create `services/dataset_intelligence/report_generator.py`**

- [ ] `assemble_report_data(dataset, profile_record, analysis_record,
      prediction_record, insight_record) -> dict` — one flat dict pulling
      together everything: dataset summary, quality score, AI readiness,
      detected domain, prediction summary, top insights, recommendations,
      feature importance, confidence scores, timestamp. This is the single
      source of truth every format below renders from — don't let PDF/Excel/
      CSV each independently reach into the DB records.
- [ ] `generate_pdf_report(report_data: dict) -> bytes` — use `reportlab`.
      Structure: title page (dataset name, timestamp), summary section,
      quality/readiness scores as simple text (a numeric score + label is
      fine — don't over-engineer gauge graphics into a PDF), domain +
      confidence, prediction summary + top 5 feature importances as a table,
      insights as a bulleted list, recommendations as a bulleted list.
      Keep it to 2-4 pages — this is an executive summary, not the full
      profile dump.
- [ ] `generate_excel_report(report_data: dict, full_profile: dict,
      full_predictions: dict | None) -> bytes` — use `openpyxl`, multiple
      sheets: `Summary` (same content as the PDF), `Column Profile` (one row
      per column from `profile.columns`), `Predictions` (one row per
      row-level prediction, if any), `Insights & Recommendations`.
- [ ] `generate_csv_export(full_predictions: dict | None) -> bytes` — flatten
      row-level predictions to CSV (`row_index, prediction, probability`).
      Return a clear error (not an empty file) if there's no prediction to
      export — a CSV of nothing is confusing, not helpful.
- [ ] `generate_json_export(report_data: dict, full_profile: dict,
      full_analysis: dict, full_predictions: dict | None,
      full_insights: dict) -> bytes` — just `json.dumps` everything, pretty-printed.

### 7.3 — API layer

**Edit `api/routes/dataset_intelligence.py`**:

- [ ] Add `GET /datasets/{id}/report?format=pdf|excel|csv|json` (query param,
      default `json`). Load all relevant records, call
      `assemble_report_data`, dispatch to the right generator, return via
      FastAPI's `Response` with the correct `media_type`
      (`application/pdf`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`,
      `text/csv`, `application/json`) and a
      `Content-Disposition: attachment; filename=...` header.
- [ ] 400 with a clear message if `format=csv` is requested but no
      prediction exists for the dataset (per the "clear error, not an empty
      file" rule above).

### 7.4 — Verify

- [ ] Generate each format for a dataset with a real prediction and one
      without (fallback case) — confirm PDF/Excel/JSON all still produce
      something sensible for the fallback case, and CSV correctly 400s.
- [ ] Open the generated PDF and Excel files (use a Python check, not just
      "did bytes come back" — e.g. `PyPDF2.PdfReader` or `openpyxl.load_workbook`
      on the returned bytes to confirm they're actually valid, parseable
      files, not corrupt output that merely has the right Content-Type header).

---

## Segment 8 — Complete the REST API Layer (spec Step 23)

**Goal**: fill in the remaining endpoints the spec explicitly lists that
earlier segments didn't cover as a side effect.

Already have (from Segments 1-7): `POST /datasets/upload` (covers
`/upload` + `/analyze` + `/predict` combined), `GET /datasets/{id}/profile`,
`GET /datasets/{id}/analysis`, `GET /datasets/{id}/predictions`,
`GET /datasets/{id}/insights`, `GET /datasets/{id}/recommendations`,
`GET /datasets/{id}/charts`, `GET /datasets/{id}/report`.

Still missing, per spec Step 23:

- [ ] **`GET /datasets`** — list all datasets for the current tenant
      (paginated, same pattern as `sellers.py`'s list endpoint). Returns
      `id, filename, status, top_domain, uploaded_at` per row — a dashboard
      landing-page list, not full detail.
- [ ] **`POST /datasets/{id}/predict`** — re-run prediction on an
      already-uploaded dataset (re-reads `dataset.storage_path`). Useful
      when: (a) the registry gained a new/better model since the original
      upload, (b) the user wants to force a re-check after a fallback. Must
      go through the *same* `select_model` fallback logic — this is not a
      backdoor to force a prediction that was correctly refused.
- [ ] **`GET /models`** — list every registered model across all domains
      (name, domain, version, accuracy, problem_type, artifact_available).
      Pull from `get_cached_registry`. This is what lets a frontend show
      "here's what we can predict" before any upload happens.
- [ ] **`GET /models/domains`** — list the full domain catalog from
      `domain_knowledge.DOMAINS` (name + example signature columns), for a
      "supported domains" help page.
- [ ] **`DELETE /datasets/{id}`** — not in the spec's endpoint list
      explicitly but implied by any real UI (Step 18 mentions a dashboard
      with datasets to manage); soft-delete via a `deleted_at` column
      (add via a new migration) rather than a hard delete, consistent with
      this project's audit-everything philosophy — a hard delete of a
      dataset with an audit trail pointing at it is a data-integrity gap.

### Verify

- [ ] Every new endpoint reachable via `TestClient`, correct permission
      gating (`Perm.DATASETS_READ`/`WRITE`), correct 404s for
      nonexistent/wrong-tenant IDs (write an explicit cross-tenant test:
      tenant A tries `GET /datasets/{tenant_B_dataset_id}` → 404, not 200
      with someone else's data — this is the single most important test in
      this whole segment given the tenancy bugs found in earlier segments).

---

## Segment 9 — Frontend Dashboard (React) (spec Steps 18, 22)

**Goal**: the actual UI. This is the largest remaining segment — budget
real time for it, don't try to compress it.

### 9.1 — Project setup

```bash
# from crm-enterprise/ root
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
npm install react-router-dom @tanstack/react-query axios recharts lucide-react
```

- [ ] Configure Tailwind (`tailwind.config.js` content globs pointing at
      `./src/**/*.{ts,tsx}`) and dark-mode (`darkMode: 'class'`, per spec
      Step 18's "dark mode compatible" requirement).
- [ ] Set up `.env` in `frontend/` with `VITE_API_BASE_URL=http://localhost:8000/api/v1`.

### 9.2 — API client layer

**Create `frontend/src/api/client.ts`**
- [ ] Axios instance with base URL from env, request interceptor attaching
      `Authorization: Bearer {token}` from wherever auth state lives
      (see 9.3), response interceptor handling 401 → redirect to login.

**Create `frontend/src/api/datasets.ts`**
- [ ] Typed functions for every backend endpoint: `uploadDataset(file)`,
      `getProfile(id)`, `getAnalysis(id)`, `getPredictions(id)`,
      `getInsights(id)`, `getRecommendations(id)`, `getCharts(id)`,
      `getReport(id, format)`, `listDatasets()`, `listModels()`.
- [ ] Mirror the Pydantic response shapes as TypeScript interfaces in
      `frontend/src/api/types.ts` — keep these hand-in-sync with
      `api/schemas.py` (no codegen tooling set up for this project; note
      that as a known manual-sync risk worth automating later with
      `openapi-typescript` against FastAPI's auto-generated OpenAPI schema).

### 9.3 — Auth

**Create `frontend/src/auth/AuthContext.tsx`**
- [ ] Login form → `POST /auth/login` → store `access_token`/`refresh_token`
      (in memory + `sessionStorage`, not `localStorage`, to limit XSS
      persistence — consistent with this project's security posture
      elsewhere). Handle the MFA-challenge 202 response path.
- [ ] Silent refresh: on 401, call `POST /auth/refresh`, retry the original
      request once.

### 9.4 — Components (spec Step 22's list)

Create each under `frontend/src/components/`:

- [ ] `Upload.tsx` — drag-and-drop zone (native HTML5 drag events, no need
      for a library), file-type/size client-side pre-check mirroring
      `core/config.py`'s `dataset_allowed_extensions`/`dataset_max_upload_mb`
      (fetch these from a small `GET /config/upload-limits` endpoint you'll
      need to add, rather than hardcoding them client-side and letting them
      drift from the backend).
- [ ] `DatasetPreview.tsx` — first N rows table (you'll need a
      `GET /datasets/{id}/preview` endpoint returning a small row sample —
      add this in Segment 8 if not already covered, since the current API
      only returns aggregate profile stats, not raw rows).
- [ ] `QualityScore.tsx` / `ConfidenceGauge.tsx` — simple radial gauge
      (`recharts`'s `RadialBarChart` is sufficient, no need for a
      dedicated gauge library) colored by the green/yellow/red bands
      Segment 2 already computes server-side — read the band from the API,
      don't recompute thresholds client-side.
- [ ] `DomainBadge.tsx` — pill/badge showing detected domain + confidence %.
- [ ] `PredictionCard.tsx` — per-row prediction + probability + top 3 SHAP
      contributors + natural-language explanation.
- [ ] `InsightCard.tsx` / `RecommendationCard.tsx` — render `Insight`/
      `Recommendation` objects, color-coded by severity/priority.
- [ ] `Charts.tsx` — one component per chart `type` from Segment 6's
      envelope (`BarChart`, `Histogram`, `Heatmap`, `PieChart`, `LineChart`
      wrappers around `recharts` primitives), dispatched by a switch on
      `chart.type`.
- [ ] `ReportDownload.tsx` — format picker + download button hitting
      `GET /datasets/{id}/report?format=...`, triggering a browser download
      (`URL.createObjectURL` on the blob response).

### 9.5 — Pages

- [ ] `pages/Login.tsx`
- [ ] `pages/DatasetList.tsx` — uses `GET /datasets`, "Upload New" CTA.
- [ ] `pages/DatasetDetail.tsx` — tabbed layout: Profile | Domain | Prediction
      | Insights | Charts | Report. Each tab lazy-loads its own data via
      React Query (`useQuery`) rather than one giant fetch-everything call —
      matches the backend's already-split endpoint design.
- [ ] Routing in `App.tsx` via `react-router-dom`, with an auth guard
      wrapping protected routes.

### 9.6 — Verify

- [ ] `npm run dev`, manually upload each of the test CSVs used in prior
      segments' backend testing, confirm every tab renders real data (not
      just "doesn't crash" — check the numbers displayed match what the
      backend test scripts printed in Segments 2-5).
- [ ] Test the fallback-dataset case in the UI specifically — confirm the
      Prediction tab shows the fallback reason clearly instead of an empty
      state that looks like a bug.
- [ ] Toggle dark mode, confirm every component respects it (this is easy
      to miss per-component — check each one, not just the shell).

---

## Segment 10 — Security / Performance / Integration Pass

**Goal**: the segment that makes everything above actually production-safe
together, not just individually functional.

### 10.1 — Test suite

**Create `tests/dataset_intelligence/` directory** with one file per
service module, mirroring the pattern used in `scripts/smoke_test.py` but
as real `pytest` tests:

- [ ] `test_validation.py` — every branch in Segment 1's `validate_upload`
      (bad extension, empty file, oversized file, bad encoding, duplicate
      headers, corrupt JSON, corrupt Excel signature).
- [ ] `test_profiler.py` — known-shape synthetic DataFrames with expected
      profile output (e.g. a DataFrame with a known constant column, known
      correlation, known class imbalance — assert the profiler reports
      exactly those).
- [ ] `test_domain_detection.py` — the healthcare/finance discrimination
      test from Segment 2's manual testing, formalized as an assertion
      (`assert result.top_domain == "Healthcare"` etc.) so it runs in CI
      going forward instead of being a one-off script.
- [ ] `test_model_selection.py` — all four fallback scenarios from Segment
      3's manual testing (accepted, artifact-unavailable, below-threshold,
      no-domain-match), formalized.
- [ ] `test_prediction.py` — the feature-count mismatch regression
      (Segment 4's bug) as an explicit test: register a deliberately
      mismatched model/metadata pair in a temp registry dir, assert
      `PredictionError` is raised with the specific diagnostic message,
      not a generic sklearn traceback.
- [ ] `test_pipeline_integration.py` — the full `ingest_and_analyze` flow,
      end-to-end, for both the healthcare and fallback cases, asserting on
      final `AnalysisResult` contents.
- [ ] `test_tenancy_isolation.py` — **the cross-tenant test flagged as
      most-important in Segment 8** — formalize it here too.

**Create `tests/conftest.py`** (if it doesn't already exist from the
earlier CRM hardening checkpoint):
- [ ] Fixtures for: in-memory SQLite engine + session, a seeded tenant, a
      seeded user with roles, an authenticated `TestClient`.

Run: `pytest tests/ -v` — should be part of CI (see 10.3).

### 10.2 — Performance

- [ ] **Chunked reading**: `services/dataset_intelligence/readers.py`'s
      `read_dataset` currently loads the whole file into a DataFrame at
      once. For files near `dataset_max_upload_mb`, add a chunked
      `pd.read_csv(..., chunksize=50_000)` path that still produces a
      single concatenated DataFrame for now (true streaming profiling is a
      bigger redesign — out of scope) but at least bounds peak memory
      during the read step itself.
- [ ] **Background execution**: the upload endpoint currently runs the
      entire pipeline (validate → read → clean → profile → domain →
      quality → readiness → select → predict → explain → insights) inline
      in the request. For large files this could exceed reasonable HTTP
      timeouts. Convert to: `POST /datasets/upload` saves the file and
      returns `202 Accepted` with `status="uploaded"` immediately, kicks
      off the rest via FastAPI `BackgroundTasks` (or, if you already have
      Redis from Segment 8/rate-limiting, a proper task queue like `arq` —
      background threads alone won't survive a process restart, mention
      this tradeoff explicitly to whoever picks this up). Frontend polls
      `GET /datasets/{id}/analysis` until `status` moves past `"analyzed"`.
- [ ] **Caching**: `get_cached_registry` already caches via `lru_cache` —
      confirm this doesn't go stale across the background-task boundary if
      you introduce a separate worker process (cache is per-process; a
      worker and the API server are different processes and need
      independent warm-up, not a shared cache).

### 10.3 — CI/CD wiring

- [ ] Extend the CI pipeline (from the earlier CRM-hardening checkpoint's
      planned `.github/workflows/ci.yml` — build it now if it still
      doesn't exist) to: install `requirements.txt` (make sure `shap`,
      `scikit-learn`, `joblib`, `reportlab`, `openpyxl` are all actually in
      it — audit this file, several were installed ad hoc during
      development and may not be pinned there yet), run
      `python3 scripts/train_demo_models.py` to populate a test registry,
      run `pytest tests/ -v`, run `npm run build` in `frontend/`.

### 10.4 — Final integration pass

- [ ] Run the **entire** flow once, start to finish, through
      `docker-compose up` (build this compose file now if it doesn't exist
      — app + postgres + redis, per the original CRM-hardening checkpoint's
      plan) rather than SQLite/in-process testing. This is the first time
      Postgres RLS (written in Segment 1 of the CRM hardening work but
      never actually exercised against real Postgres, since this sandbox
      couldn't install it) gets to run for real — treat this as a required
      checkpoint, not optional polish.
- [ ] Re-run every manual test script from Segments 1-4
      (`scripts/smoke_test.py`, the ad hoc domain-detection/model-selection/
      prediction test blocks documented in the checkpoint) against the
      dockerized stack.
- [ ] Update `README.md` to describe the finished feature end-to-end for a
      new developer, replacing the original demo-app README's dataset-
      intelligence-shaped gap.

---

## Summary checklist (tick off as segments complete)

- [x] Segment 5 — Insights + Recommendations
- [x] Segment 6 — Visualizations
- [x] Segment 7 — Report Export
- [x] Segment 8 — Remaining REST endpoints
- [x] Segment 9 — React frontend
- [x] Segment 10 — Tests, performance, CI, Docker integration pass

## All 10 segments complete.

See `CHECKPOINT_DATASET_INTELLIGENCE.md` for the full build history,
every bug found and fixed along the way, and the honest list of what's
left for outside this sandbox (Docker build/run, browser rendering,
load testing).
