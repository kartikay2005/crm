"""
Dataset Intelligence Engine — Segment 1 orchestration.

Wires together: File Validation -> Read Dataset -> Clean Dataset -> Profile
Dataset (the first four boxes of the pipeline diagram in the spec). Later
segments will extend this same Dataset row's `status` field through the
remaining stages (domain detection, model selection, prediction, ...).

Raw files are stored on disk under core.config.dataset_upload_dir, namespaced
by tenant_id/dataset_id so tenants can never collide or traverse into each
other's uploads even via a crafted filename.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import joblib

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_settings
from core.logging_config import get_logger
from core.tenancy import current_tenant
from models.base import new_uuid
from models.dataset_intelligence import (
    Dataset, DatasetAnalysisRecord, DatasetInsightRecord, DatasetPredictionRecord, DatasetProfileRecord,
)
from services.audit import audit_event
from services.dataset_intelligence.domain_detection import detect_domain
from services.dataset_intelligence.explainability import ExplainabilityError, explain
from services.dataset_intelligence.insight_generator import generate_insights
from services.dataset_intelligence.model_registry import get_cached_registry, reload_registry
from services.dataset_intelligence.model_selection import select_model
from services.dataset_intelligence.prediction import PredictionError, predict_dataset
from services.dataset_intelligence.profiler import DatasetProfile, profile_dataset
from services.dataset_intelligence.quality_readiness import compute_quality_score, compute_readiness_score
from services.dataset_intelligence.readers import DatasetReadError, clean_dataset, read_dataset
from services.dataset_intelligence.recommendation_engine import generate_recommendations
from services.dataset_intelligence.validation import validate_upload

_log = get_logger(__name__)
_settings = get_settings()

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def _sanitize_filename(filename: str) -> str:
    """Defense-in-depth against path traversal / injection via filename.
    We never use the sanitized name to build a filesystem path directly
    anyway (see _storage_path, which uses dataset_id, not the filename) —
    this is for the display name and any future logging/export use."""
    base = Path(filename).name  # strips any directory components
    return _UNSAFE_FILENAME_CHARS.sub("_", base)[:255] or "upload"


def _storage_path(tenant_id: str, dataset_id: str, extension: str) -> Path:
    root = Path(_settings.dataset_upload_dir)
    tenant_dir = root / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir / f"{dataset_id}{extension}"


class DatasetIntelligenceError(Exception):
    """Base class for pipeline errors with a friendly, user-facing message
    and a machine-readable `stage` so the frontend can show the right icon."""

    def __init__(self, message: str, stage: str, issues: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.issues = issues or []


@dataclass
class AnalysisResult:
    """Bundles everything Segments 1-5 produce for one dataset, so callers
    (API routes) get a complete picture in one return value instead of
    juggling several separate objects."""
    domain_result: object      # DomainDetectionResult
    quality: object            # ScoreBreakdown
    readiness: object          # ScoreBreakdown
    model_selection: object    # ModelSelectionResult
    prediction: object = None       # PredictionResult | None — None means fallback path, not an error
    explainability: object = None   # ExplainabilityResult | None
    insights: object = None         # InsightBundle | None — None only on an actual generation failure
    recommendations: list = None    # list[Recommendation]


def ingest_and_analyze(db: Session, filename: str, content: bytes, user_id: str) -> tuple[Dataset, DatasetProfile, AnalysisResult]:
    """Run Steps 1-6 of the pipeline end-to-end: validate, read, clean,
    profile, detect domain, score quality + AI readiness. Persists a
    Dataset row, a DatasetProfileRecord, and a DatasetAnalysisRecord.
    Returns all three levels of result so the caller can build a response
    without extra DB reads.

    Never lets a raw exception escape — every failure mode is translated
    into a DatasetIntelligenceError with a friendly message, and the
    Dataset row (if one was already created) is marked status="failed"
    with the reason recorded, so the upload isn't silently lost.
    """
    ctx = current_tenant()
    safe_name = _sanitize_filename(filename)

    validation = validate_upload(safe_name, content)
    if not validation.ok:
        messages = [i.message for i in validation.error_issues()]
        audit_event(db, "dataset.upload_rejected", payload={"filename": safe_name, "issues": messages})
        raise DatasetIntelligenceError(
            "The file didn't pass validation.", stage="validation", issues=messages,
        )

    dataset = Dataset(
        id=new_uuid(),
        tenant_id=ctx.tenant_id,
        uploaded_by_user_id=user_id,
        original_filename=safe_name,
        extension=validation.extension,
        size_bytes=validation.size_bytes,
        storage_path="",  # filled in below once we know the dataset id
        status="uploaded",
    )
    db.add(dataset)
    db.flush()  # assigns nothing extra here, but keeps the row visible for FK use below

    storage_path = _storage_path(ctx.tenant_id, dataset.id, validation.extension)
    try:
        storage_path.write_bytes(content)
        dataset.storage_path = str(storage_path)
    except OSError as exc:
        dataset.status = "failed"
        dataset.error_message = "Could not save the uploaded file to storage."
        db.flush()
        _log.error("dataset.storage_write_failed", dataset_id=dataset.id, error=str(exc))
        raise DatasetIntelligenceError(
            "The file couldn't be saved. Please try again.", stage="storage",
        ) from exc

    try:
        df = read_dataset(safe_name, content, validation)
    except DatasetReadError as exc:
        dataset.status = "failed"
        dataset.error_message = str(exc)
        db.flush()
        audit_event(db, "dataset.read_failed", resource_type="dataset", resource_id=dataset.id,
                    payload={"error": str(exc)})
        raise DatasetIntelligenceError(str(exc), stage="read") from exc

    dataset.status = "validated"

    try:
        cleaned = clean_dataset(df)
        dataset.status = "cleaned"

        profile = profile_dataset(cleaned.df, dataset_name=safe_name, cleaning_notes=cleaned.cleaning_notes)
        dataset.status = "profiled"
    except Exception as exc:  # noqa: BLE001
        dataset.status = "failed"
        dataset.error_message = "An unexpected error occurred while analyzing the dataset."
        db.flush()
        _log.error("dataset.profiling_failed", dataset_id=dataset.id, error=str(exc), exc_info=True)
        audit_event(db, "dataset.profiling_failed", resource_type="dataset", resource_id=dataset.id)
        raise DatasetIntelligenceError(
            "The file was read successfully but couldn't be analyzed. "
            "Please check its contents and try again.", stage="profiling",
        ) from exc

    profile_record = DatasetProfileRecord(
        id=new_uuid(), tenant_id=ctx.tenant_id, dataset_id=dataset.id,
        profile_json=profile.to_dict(),
    )
    db.add(profile_record)
    db.flush()

    try:
        domain_result = detect_domain(cleaned.df, profile)

        registry = get_cached_registry(_settings.model_registry_dir)
        selection = select_model(domain_result.top_domain, profile, list(registry))

        quality = compute_quality_score(profile)

        # Feed model selection's real schema-match score into readiness
        # instead of the domain-completeness proxy, whenever we actually
        # attempted selection and found at least one candidate to score
        # (even if it was ultimately rejected by the fallback threshold —
        # "we checked and the best candidate was 62%" is a better readiness
        # signal than "we didn't check at all").
        real_schema_match = selection.considered[0].match_pct if selection.considered else None
        readiness = compute_readiness_score(profile, domain_result, quality, real_schema_match)

        dataset.status = "analyzed"
    except Exception as exc:  # noqa: BLE001
        # Domain detection/scoring failing doesn't invalidate the profile
        # that's already been persisted — degrade gracefully rather than
        # losing the whole upload over a scoring bug.
        dataset.status = "profiled"
        _log.error("dataset.analysis_failed", dataset_id=dataset.id, error=str(exc), exc_info=True)
        audit_event(db, "dataset.analysis_failed", resource_type="dataset", resource_id=dataset.id)
        raise DatasetIntelligenceError(
            "The dataset was profiled successfully, but domain/quality analysis failed. "
            "The profile is still available.", stage="analysis",
        ) from exc

    analysis_record = DatasetAnalysisRecord(
        id=new_uuid(), tenant_id=ctx.tenant_id, dataset_id=dataset.id,
        top_domain=domain_result.top_domain,
        domain_confidence=domain_result.top_confidence,
        domain_band=domain_result.band,
        domain_scores_json=[
            {"domain": s.domain, "confidence": s.confidence, "band": s.band,
             "component_scores": s.component_scores, "matched_columns": s.matched_columns}
            for s in domain_result.all_scores
        ],
        quality_score=quality.score, quality_label=quality.label,
        quality_json={"components": quality.components, "notes": quality.notes},
        readiness_score=readiness.score, readiness_label=readiness.label,
        readiness_json={"components": readiness.components, "notes": readiness.notes},
        selected_model_name=selection.selected_model.name if selection.selected_model else None,
        selected_model_version=selection.selected_model.version if selection.selected_model else None,
        schema_match_pct=selection.selected_match.match_pct if selection.selected_match else (
            selection.considered[0].match_pct if selection.considered else None
        ),
        fallback_reason=selection.fallback_reason,
        considered_models_json=[
            {"model_name": m.model_name, "match_pct": m.match_pct,
             "matched_required": m.matched_required, "missing_required": m.missing_required,
             "matched_optional": m.matched_optional, "dtype_mismatches": m.dtype_mismatches,
             "missing_data_violations": m.missing_data_violations}
            for m in selection.considered
        ],
    )
    db.add(analysis_record)
    db.flush()

    # --- Segment 4: Prediction + Explainability ---
    # Only runs when model_selection actually selected a model — the
    # fallback path (selection.selected_model is None) means "don't
    # predict," and that's respected here unconditionally, not just in
    # model_selection.py's own logic. This is the second enforcement point
    # for "never force predictions," on purpose — defense in depth for a
    # requirement the spec treats as non-negotiable.
    prediction_result = None
    explainability_result = None
    if selection.selected_model is not None:
        try:
            prediction_result = predict_dataset(cleaned.df, selection.selected_model)
            estimator = joblib.load(selection.selected_model.artifact_path)
            X, _ = _prediction_feature_matrix(cleaned.df, selection.selected_model)
            explainability_result = explain(
                estimator, X, selection.selected_model.target_variable,
                selection.selected_model.problem_type,
            )
            dataset.status = "predicted"
        except (PredictionError, ExplainabilityError) as exc:
            # Prediction/explanation failing doesn't invalidate everything
            # already computed — degrade gracefully. This is a real,
            # expected failure mode (e.g. feature values genuinely outside
            # training range), not a bug, so it's logged at warning level.
            _log.warning("dataset.prediction_failed", dataset_id=dataset.id, error=str(exc))
            audit_event(db, "dataset.prediction_failed", resource_type="dataset",
                        resource_id=dataset.id, payload={"error": str(exc)})

    if prediction_result is not None:
        prediction_record = DatasetPredictionRecord(
            id=new_uuid(), tenant_id=ctx.tenant_id, dataset_id=dataset.id,
            model_name=prediction_result.model_name, model_version=prediction_result.model_version,
            problem_type=prediction_result.problem_type, target_variable=prediction_result.target_variable,
            n_rows_predicted=prediction_result.n_rows_predicted,
            rows_dropped_missing_features=prediction_result.rows_dropped_missing_features,
            inference_time_seconds=prediction_result.inference_time_seconds,
            rows_per_second=prediction_result.rows_per_second,
            prediction_distribution_json=prediction_result.prediction_distribution,
            row_predictions_json=[
                {"row_index": r.row_index, "prediction": r.prediction, "probability": r.probability,
                 "class_probabilities": r.class_probabilities}
                for r in prediction_result.row_predictions
            ],
            explainer_type=explainability_result.explainer_type if explainability_result else None,
            global_importance_json=explainability_result.global_importance if explainability_result else {},
            local_explanations_json=[
                {"row_index": e.row_index, "natural_language": e.natural_language,
                 "top_contributors": [
                     {"feature": c.feature, "value": c.value, "shap_value": c.shap_value,
                      "direction": c.direction}
                     for c in e.top_contributors
                 ]}
                for e in (explainability_result.local_explanations if explainability_result else [])
            ],
            explainability_error=None if explainability_result else "Explanation generation failed; predictions are still valid.",
        )
        db.add(prediction_record)
        db.flush()

    # --- Segment 5: Insights + Recommendations ---
    # Unconditional, unlike prediction — this is the one step that DOES run
    # on the fallback path. The spec's own fallback message promises
    # "exploratory insights are shown" when no model is selected, so this
    # step must not be gated behind selection.selected_model like Segment 4
    # is. Wrapped in its own try/except so an insight-generation bug can't
    # take down a pipeline run that's otherwise fully succeeded.
    insight_bundle = None
    recommendations = []
    try:
        insight_bundle = generate_insights(
            cleaned.df, profile, domain_result, quality, readiness, prediction_result,
        )
        recommendations = generate_recommendations(
            domain_result.top_domain, profile, prediction_result, quality,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("dataset.insights_failed", dataset_id=dataset.id, error=str(exc))
        audit_event(db, "dataset.insights_failed", resource_type="dataset",
                    resource_id=dataset.id, payload={"error": str(exc)})
        insight_bundle = None  # response builder must handle this as "not available", not crash

    if insight_bundle is not None:
        insight_record = DatasetInsightRecord(
            id=new_uuid(), tenant_id=ctx.tenant_id, dataset_id=dataset.id,
            summary=insight_bundle.summary,
            insights_json={
                "key_findings": [_insight_to_dict(i) for i in insight_bundle.key_findings],
                "risks": [_insight_to_dict(i) for i in insight_bundle.risks],
                "hidden_trends": [_insight_to_dict(i) for i in insight_bundle.hidden_trends],
                "anomalies": [_insight_to_dict(i) for i in insight_bundle.anomalies],
            },
            recommendations_json=[
                {"text": r.text, "priority": r.priority, "domain": r.domain, "rationale": r.rationale}
                for r in recommendations
            ],
        )
        db.add(insight_record)
        db.flush()

    audit_event(
        db, "dataset.analyzed", resource_type="dataset", resource_id=dataset.id,
        payload={
            "filename": safe_name, "rows": profile.n_rows, "columns": profile.n_columns,
            "domain": domain_result.top_domain, "domain_confidence": domain_result.top_confidence,
            "quality_score": quality.score, "readiness_score": readiness.score,
            "selected_model": analysis_record.selected_model_name,
            "fallback_reason": selection.fallback_reason,
            "predicted": prediction_result is not None,
            "insights_generated": insight_bundle is not None,
        },
    )
    _log.info(
        "dataset.pipeline_complete", dataset_id=dataset.id, rows=profile.n_rows, cols=profile.n_columns,
        domain=domain_result.top_domain, quality=quality.score, readiness=readiness.score,
        selected_model=analysis_record.selected_model_name, predicted=prediction_result is not None,
        insights_generated=insight_bundle is not None,
    )

    return dataset, profile, AnalysisResult(
        domain_result=domain_result, quality=quality, readiness=readiness,
        model_selection=selection, prediction=prediction_result, explainability=explainability_result,
        insights=insight_bundle, recommendations=recommendations,
    )


def _insight_to_dict(insight) -> dict:
    return {
        "category": insight.category, "text": insight.text,
        "severity": insight.severity, "supporting_data": insight.supporting_data,
    }


def _prediction_feature_matrix(df, model):
    """Thin re-export so pipeline.py doesn't need a second import path for
    the same feature-matrix builder prediction.py uses internally — keeps
    the explainability step working against exactly the same X that
    estimator.predict() saw."""
    from services.dataset_intelligence.prediction import _build_feature_matrix
    return _build_feature_matrix(df, model)


class DatasetNotFoundError(Exception):
    """Distinct from DatasetIntelligenceError — this means the dataset row
    itself (or its stored file) is missing, not that the pipeline failed
    on otherwise-valid input."""


def repredict_dataset(db: Session, dataset_id: str) -> tuple[Dataset, AnalysisResult]:
    """Re-run model selection + prediction + explainability on an
    already-uploaded dataset, against a FRESHLY reloaded registry (not the
    process-cached one — see model_registry.reload_registry's docstring
    for why that distinction matters here specifically).

    Use cases this exists for: the registry gained a new/better model
    since the original upload, or the user wants to force a re-check after
    a fallback. This does NOT bypass the fallback threshold — it goes
    through the exact same select_model() logic as the original upload, so
    a dataset that correctly got no prediction before will still correctly
    get no prediction now unless something in the registry genuinely
    changed. Re-running this is not a backdoor to force a prediction that
    was correctly refused.

    Domain detection is NOT re-run — the dataset's content hasn't changed,
    so its domain hasn't either; only the registry might have. Insights
    and recommendations are NOT regenerated by this endpoint since they
    don't depend on model selection changing (Segment 5's insights already
    correctly cover the fallback case) — only the prediction-dependent
    records are updated here.
    """
    ctx = current_tenant()
    _settings_local = get_settings()

    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.deleted_at is not None:
        raise DatasetNotFoundError(f"Dataset {dataset_id} not found")

    try:
        content = Path(dataset.storage_path).read_bytes()
    except OSError as exc:
        raise DatasetIntelligenceError(
            "The original uploaded file is no longer available in storage.", stage="storage",
        ) from exc

    validation = validate_upload(dataset.original_filename, content)
    if not validation.ok:
        raise DatasetIntelligenceError(
            "The stored file no longer passes validation.", stage="validation",
        )
    try:
        raw_df = read_dataset(dataset.original_filename, content, validation)
        cleaned = clean_dataset(raw_df)
        profile = profile_dataset(cleaned.df, dataset_name=dataset.original_filename,
                                   cleaning_notes=cleaned.cleaning_notes)
    except DatasetReadError as exc:
        raise DatasetIntelligenceError(str(exc), stage="read") from exc

    analysis_record = db.execute(
        select(DatasetAnalysisRecord).where(DatasetAnalysisRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if analysis_record is None:
        raise DatasetIntelligenceError(
            "This dataset has no prior analysis to re-predict from — upload it fresh instead.",
            stage="analysis",
        )
    domain_top = analysis_record.top_domain

    registry = reload_registry(_settings_local.model_registry_dir)
    selection = select_model(domain_top, profile, list(registry))

    prediction_result = None
    explainability_result = None
    if selection.selected_model is not None:
        try:
            prediction_result = predict_dataset(cleaned.df, selection.selected_model)
            estimator = joblib.load(selection.selected_model.artifact_path)
            X, _ = _prediction_feature_matrix(cleaned.df, selection.selected_model)
            explainability_result = explain(
                estimator, X, selection.selected_model.target_variable,
                selection.selected_model.problem_type,
            )
        except (PredictionError, ExplainabilityError) as exc:
            _log.warning("dataset.repredict_failed", dataset_id=dataset.id, error=str(exc))
            audit_event(db, "dataset.repredict_failed", resource_type="dataset",
                        resource_id=dataset.id, payload={"error": str(exc)})

    # Update the analysis record's model-selection fields in place —
    # domain/quality/readiness are untouched since content hasn't changed.
    analysis_record.selected_model_name = selection.selected_model.name if selection.selected_model else None
    analysis_record.selected_model_version = selection.selected_model.version if selection.selected_model else None
    analysis_record.schema_match_pct = (
        selection.selected_match.match_pct if selection.selected_match else
        (selection.considered[0].match_pct if selection.considered else None)
    )
    analysis_record.fallback_reason = selection.fallback_reason
    analysis_record.considered_models_json = [
        {"model_name": m.model_name, "match_pct": m.match_pct,
         "matched_required": m.matched_required, "missing_required": m.missing_required,
         "matched_optional": m.matched_optional, "dtype_mismatches": m.dtype_mismatches,
         "missing_data_violations": m.missing_data_violations}
        for m in selection.considered
    ]

    # Upsert the prediction record — delete any prior one and insert fresh,
    # simpler and less error-prone than field-by-field update given how
    # many columns DatasetPredictionRecord has.
    existing_pred = db.execute(
        select(DatasetPredictionRecord).where(DatasetPredictionRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if existing_pred is not None:
        db.delete(existing_pred)
        db.flush()

    if prediction_result is not None:
        db.add(DatasetPredictionRecord(
            id=new_uuid(), tenant_id=ctx.tenant_id, dataset_id=dataset.id,
            model_name=prediction_result.model_name, model_version=prediction_result.model_version,
            problem_type=prediction_result.problem_type, target_variable=prediction_result.target_variable,
            n_rows_predicted=prediction_result.n_rows_predicted,
            rows_dropped_missing_features=prediction_result.rows_dropped_missing_features,
            inference_time_seconds=prediction_result.inference_time_seconds,
            rows_per_second=prediction_result.rows_per_second,
            prediction_distribution_json=prediction_result.prediction_distribution,
            row_predictions_json=[
                {"row_index": r.row_index, "prediction": r.prediction, "probability": r.probability,
                 "class_probabilities": r.class_probabilities}
                for r in prediction_result.row_predictions
            ],
            explainer_type=explainability_result.explainer_type if explainability_result else None,
            global_importance_json=explainability_result.global_importance if explainability_result else {},
            local_explanations_json=[
                {"row_index": e.row_index, "natural_language": e.natural_language,
                 "top_contributors": [
                     {"feature": c.feature, "value": c.value, "shap_value": c.shap_value,
                      "direction": c.direction}
                     for c in e.top_contributors
                 ]}
                for e in (explainability_result.local_explanations if explainability_result else [])
            ],
            explainability_error=None if explainability_result else "Explanation generation failed; predictions are still valid.",
        ))
        dataset.status = "predicted"
    else:
        dataset.status = "analyzed"

    db.flush()
    audit_event(
        db, "dataset.repredicted", resource_type="dataset", resource_id=dataset.id,
        payload={"selected_model": analysis_record.selected_model_name,
                 "fallback_reason": selection.fallback_reason},
    )
    _log.info("dataset.repredict_complete", dataset_id=dataset.id,
              selected_model=analysis_record.selected_model_name, predicted=prediction_result is not None)

    return dataset, AnalysisResult(
        domain_result=None, quality=None, readiness=None,
        model_selection=selection, prediction=prediction_result, explainability=explainability_result,
    )
