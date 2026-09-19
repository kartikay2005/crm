"""
Dataset Intelligence Engine — Segment 1 API surface.

POST /datasets/upload — runs validate -> read -> clean -> profile in one
call and returns the full profile. This covers the spec's /upload and
/analyze endpoints as a single request for now (Segment 1 has nothing
async yet); Segment 8 will split them properly once prediction is in the
mix and the pipeline is genuinely long-running enough to need polling.

GET /datasets/{id}/profile — re-fetch a previously computed profile.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import get_db, get_tenant_context, require_permission
from services.audit import audit_event
from api.schemas import (
    ChartOut, DatasetListItemOut, DatasetListResponse, DatasetProfileOut, DatasetUploadResponse,
    DomainInfoOut, DomainScoreOut, InsightBundleOut, InsightsSummaryOut, ModelInfoOut,
    ModelSelectionOut, PredictionSummaryOut, RecommendationOut, RepredictResponse,
    SchemaMatchOut, ScoreBreakdownOut,
)
from core.config import get_settings
from models.dataset_intelligence import (
    Dataset, DatasetAnalysisRecord, DatasetInsightRecord, DatasetPredictionRecord, DatasetProfileRecord,
)
from services.dataset_intelligence import report_generator
from services.dataset_intelligence import visualization as viz_service
from services.dataset_intelligence.domain_knowledge import DOMAINS
from models.rbac import Perm
from services.dataset_intelligence.model_registry import get_cached_registry
from services.dataset_intelligence.pipeline import (
    DatasetIntelligenceError, DatasetNotFoundError, ingest_and_analyze, repredict_dataset,
)
from services.dataset_intelligence.profiler import profile_dataset
from services.dataset_intelligence.readers import DatasetReadError, clean_dataset, read_dataset
from services.dataset_intelligence.validation import validate_upload

router = APIRouter(prefix="/datasets", tags=["dataset-intelligence"])
models_router = APIRouter(prefix="/models", tags=["dataset-intelligence"])
_settings = get_settings()


def _get_active_dataset(db: Session, dataset_id: str) -> Dataset:
    """Fetch a Dataset, treating a soft-deleted row the same as a missing
    one — used by every route that operates on a single dataset, so
    DELETE /datasets/{id} actually takes effect everywhere consistently
    rather than only hiding the dataset from the list endpoint."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    return dataset


@router.get("", response_model=DatasetListResponse)
def list_datasets(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    """Dashboard landing-page list — id/filename/status/domain/timestamp
    per row, not full detail (matches the pattern used by sellers.py's
    list endpoint). Soft-deleted datasets are excluded."""
    base_query = select(Dataset).where(Dataset.deleted_at.is_(None))
    total = db.execute(
        select(func.count()).select_from(Dataset).where(Dataset.deleted_at.is_(None))
    ).scalar_one()
    rows = db.execute(
        base_query.order_by(Dataset.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    items = []
    for dataset in rows:
        analysis = db.execute(
            select(DatasetAnalysisRecord.top_domain).where(DatasetAnalysisRecord.dataset_id == dataset.id)
        ).scalar_one_or_none()
        items.append(DatasetListItemOut(
            dataset_id=dataset.id, filename=dataset.original_filename,
            status=dataset.status, top_domain=analysis, uploaded_at=dataset.created_at,
        ))

    return DatasetListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/upload", response_model=DatasetUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    file: UploadFile = File(...),
    tenant_ctx=Depends(get_tenant_context),
    claims: dict = Depends(require_permission(Perm.DATASETS_WRITE)),
    db: Session = Depends(get_db),
):
    content = await file.read()

    # Cheap early rejection before we even touch the pipeline — avoids
    # buffering huge uploads further than necessary. The pipeline's own
    # validate_upload() re-checks this (defense in depth / testability),
    # so this is purely a fast-fail optimization.
    max_bytes = _settings.dataset_max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {_settings.dataset_max_upload_mb} MB limit.",
        )

    try:
        dataset, profile, analysis = ingest_and_analyze(db, file.filename or "upload", content, claims["sub"])
    except DatasetIntelligenceError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": exc.message, "stage": exc.stage, "issues": exc.issues},
        )

    return DatasetUploadResponse(
        dataset_id=dataset.id, filename=dataset.original_filename,
        status=dataset.status, profile=DatasetProfileOut(**profile.to_dict()),
        top_domain=analysis.domain_result.top_domain,
        domain_confidence=analysis.domain_result.top_confidence,
        domain_band=analysis.domain_result.band,
        domain_scores=[
            DomainScoreOut(domain=s.domain, confidence=s.confidence, band=s.band,
                           component_scores=s.component_scores, matched_columns=s.matched_columns)
            for s in analysis.domain_result.all_scores
        ],
        quality=ScoreBreakdownOut(score=analysis.quality.score, label=analysis.quality.label,
                                   components=analysis.quality.components, notes=analysis.quality.notes),
        readiness=ScoreBreakdownOut(score=analysis.readiness.score, label=analysis.readiness.label,
                                     components=analysis.readiness.components, notes=analysis.readiness.notes),
        model_selection=ModelSelectionOut(
            selected_model_name=analysis.model_selection.selected_model.name
                if analysis.model_selection.selected_model else None,
            selected_model_version=analysis.model_selection.selected_model.version
                if analysis.model_selection.selected_model else None,
            schema_match_pct=analysis.model_selection.selected_match.match_pct
                if analysis.model_selection.selected_match else (
                    analysis.model_selection.considered[0].match_pct
                    if analysis.model_selection.considered else None
                ),
            fallback_reason=analysis.model_selection.fallback_reason,
            considered=[
                SchemaMatchOut(model_name=m.model_name, match_pct=m.match_pct,
                               matched_required=m.matched_required, missing_required=m.missing_required,
                               matched_optional=m.matched_optional, dtype_mismatches=m.dtype_mismatches,
                               missing_data_violations=m.missing_data_violations)
                for m in analysis.model_selection.considered
            ],
        ),
        prediction_summary=PredictionSummaryOut(
            model_name=analysis.prediction.model_name, model_version=analysis.prediction.model_version,
            problem_type=analysis.prediction.problem_type, target_variable=analysis.prediction.target_variable,
            n_rows_predicted=analysis.prediction.n_rows_predicted,
            rows_dropped_missing_features=analysis.prediction.rows_dropped_missing_features,
            inference_time_seconds=analysis.prediction.inference_time_seconds,
            rows_per_second=analysis.prediction.rows_per_second,
            prediction_distribution=analysis.prediction.prediction_distribution,
            top_global_features=dict(list((analysis.explainability.global_importance
                                            if analysis.explainability else {}).items())[:5]),
        ) if analysis.prediction is not None else None,
        insights_summary=InsightsSummaryOut(
            summary=analysis.insights.summary,
            top_findings=[i.text for i in (analysis.insights.key_findings[:5] if analysis.insights else [])],
            top_recommendations=[
                RecommendationOut(text=r.text, priority=r.priority, domain=r.domain, rationale=r.rationale)
                for r in (analysis.recommendations or [])[:5]
            ],
        ) if analysis.insights is not None else None,
    )


@router.get("/{dataset_id}/profile", response_model=DatasetProfileOut)
def get_dataset_profile(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    dataset = _get_active_dataset(db, dataset_id)

    record = db.execute(
        select(DatasetProfileRecord).where(DatasetProfileRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not yet available for this dataset")

    return DatasetProfileOut(**record.profile_json)


@router.get("/{dataset_id}/insights", response_model=InsightBundleOut)
def get_dataset_insights(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    dataset = _get_active_dataset(db, dataset_id)

    record = db.execute(
        select(DatasetInsightRecord).where(DatasetInsightRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No insights available for this dataset — either analysis hasn't "
            "completed yet, or insight generation failed. Check "
            "GET /datasets/{id}/analysis for pipeline status.",
        )

    return InsightBundleOut(
        summary=record.summary,
        key_findings=record.insights_json.get("key_findings", []),
        risks=record.insights_json.get("risks", []),
        hidden_trends=record.insights_json.get("hidden_trends", []),
        anomalies=record.insights_json.get("anomalies", []),
    )


@router.get("/{dataset_id}/recommendations", response_model=list[RecommendationOut])
def get_dataset_recommendations(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    dataset = _get_active_dataset(db, dataset_id)

    record = db.execute(
        select(DatasetInsightRecord).where(DatasetInsightRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recommendations available for this dataset")

    return [RecommendationOut(**r) for r in record.recommendations_json]


@router.get("/{dataset_id}/report")
def get_dataset_report(
    dataset_id: str,
    format: str = Query("json", pattern=r"^(pdf|excel|csv|json)$"),
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    dataset = _get_active_dataset(db, dataset_id)

    profile_record = db.execute(
        select(DatasetProfileRecord).where(DatasetProfileRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    analysis_record = db.execute(
        select(DatasetAnalysisRecord).where(DatasetAnalysisRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if profile_record is None or analysis_record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This dataset hasn't finished analysis yet — no report can be generated.",
        )
    prediction_record = db.execute(
        select(DatasetPredictionRecord).where(DatasetPredictionRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    insight_record = db.execute(
        select(DatasetInsightRecord).where(DatasetInsightRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()

    report_data = report_generator.assemble_report_data(
        dataset, profile_record, analysis_record, prediction_record, insight_record,
    )

    full_predictions = None
    if prediction_record is not None:
        full_predictions = {
            "model_name": prediction_record.model_name, "model_version": prediction_record.model_version,
            "problem_type": prediction_record.problem_type, "target_variable": prediction_record.target_variable,
            "n_rows_predicted": prediction_record.n_rows_predicted,
            "prediction_distribution": prediction_record.prediction_distribution_json,
            "row_predictions": prediction_record.row_predictions_json,
            "global_importance": prediction_record.global_importance_json,
        }
    full_analysis = {
        "top_domain": analysis_record.top_domain, "domain_confidence": analysis_record.domain_confidence,
        "domain_band": analysis_record.domain_band, "domain_scores": analysis_record.domain_scores_json,
        "quality_score": analysis_record.quality_score, "quality_label": analysis_record.quality_label,
        "readiness_score": analysis_record.readiness_score, "readiness_label": analysis_record.readiness_label,
        "selected_model_name": analysis_record.selected_model_name,
        "fallback_reason": analysis_record.fallback_reason,
    }
    full_insights = {
        "summary": insight_record.summary if insight_record else "",
        "insights": insight_record.insights_json if insight_record else {},
        "recommendations": insight_record.recommendations_json if insight_record else [],
    }

    stem = dataset.original_filename.rsplit(".", 1)[0] if "." in dataset.original_filename else dataset.original_filename
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
    try:
        if format == "pdf":
            content = report_generator.generate_pdf_report(report_data)
            media_type, filename = "application/pdf", f"{safe_name}_report.pdf"
        elif format == "excel":
            content = report_generator.generate_excel_report(
                report_data, profile_record.profile_json, full_predictions,
            )
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"{safe_name}_report.xlsx"
        elif format == "csv":
            content = report_generator.generate_csv_export(full_predictions)
            media_type, filename = "text/csv", f"{safe_name}_predictions.csv"
        else:  # json
            content = report_generator.generate_json_export(
                report_data, profile_record.profile_json, full_analysis, full_predictions, full_insights,
            )
            media_type, filename = "application/json", f"{safe_name}_report.json"
    except report_generator.ReportGenerationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{dataset_id}/charts", response_model=list[ChartOut])
def get_dataset_charts(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    """Charts are computed on demand, not persisted (see
    services/dataset_intelligence/visualization.py's module docstring for
    why). This means re-reading and re-cleaning the stored raw file rather
    than reconstructing a DatasetProfile from persisted JSON — cheaper to
    build than to maintain a JSON-to-dataclass deserializer, and
    guaranteed consistent with the actual stored file rather than a
    potentially-stale profile snapshot."""
    dataset = _get_active_dataset(db, dataset_id)

    try:
        content = Path(dataset.storage_path).read_bytes()
    except OSError:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The original uploaded file is no longer available in storage.",
        )

    validation = validate_upload(dataset.original_filename, content)
    if not validation.ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "The stored file no longer passes validation — it may be corrupted.")
    try:
        raw_df = read_dataset(dataset.original_filename, content, validation)
        cleaned = clean_dataset(raw_df)
        profile = profile_dataset(cleaned.df, dataset_name=dataset.original_filename,
                                   cleaning_notes=cleaned.cleaning_notes)
    except DatasetReadError:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "Could not re-read the stored file for chart generation.")

    pred_record = db.execute(
        select(DatasetPredictionRecord).where(DatasetPredictionRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()

    charts = viz_service.build_all_charts(
        cleaned.df, profile,
        global_importance=pred_record.global_importance_json if pred_record else None,
        problem_type=pred_record.problem_type if pred_record else None,
        target_variable=pred_record.target_variable if pred_record else None,
        prediction_distribution=pred_record.prediction_distribution_json if pred_record else None,
        row_predictions=pred_record.row_predictions_json if pred_record else None,
    )
    return [ChartOut(chart_id=c.chart_id, type=c.type, title=c.title, data=c.data) for c in charts]


@router.get("/{dataset_id}/predictions")
def get_dataset_predictions(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    dataset = _get_active_dataset(db, dataset_id)

    record = db.execute(
        select(DatasetPredictionRecord).where(DatasetPredictionRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No prediction available for this dataset — either the pipeline "
            "chose the fallback (insights-only) path, or prediction hasn't "
            "completed yet. Check GET /datasets/{id}/analysis for the reason.",
        )

    return {
        "dataset_id": dataset_id,
        "model_name": record.model_name,
        "model_version": record.model_version,
        "problem_type": record.problem_type,
        "target_variable": record.target_variable,
        "n_rows_predicted": record.n_rows_predicted,
        "rows_dropped_missing_features": record.rows_dropped_missing_features,
        "inference_time_seconds": record.inference_time_seconds,
        "rows_per_second": record.rows_per_second,
        "prediction_distribution": record.prediction_distribution_json,
        "row_predictions": record.row_predictions_json,
        "explainer_type": record.explainer_type,
        "global_importance": record.global_importance_json,
        "local_explanations": record.local_explanations_json,
        "explainability_error": record.explainability_error,
    }


@router.get("/{dataset_id}/analysis")
def get_dataset_analysis(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
    db: Session = Depends(get_db),
):
    dataset = _get_active_dataset(db, dataset_id)

    record = db.execute(
        select(DatasetAnalysisRecord).where(DatasetAnalysisRecord.dataset_id == dataset_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not yet available for this dataset")

    return {
        "dataset_id": dataset_id,
        "top_domain": record.top_domain,
        "domain_confidence": record.domain_confidence,
        "domain_band": record.domain_band,
        "domain_scores": record.domain_scores_json,
        "quality": {"score": record.quality_score, "label": record.quality_label, **record.quality_json},
        "readiness": {"score": record.readiness_score, "label": record.readiness_label, **record.readiness_json},
        "model_selection": {
            "selected_model_name": record.selected_model_name,
            "selected_model_version": record.selected_model_version,
            "schema_match_pct": record.schema_match_pct,
            "fallback_reason": record.fallback_reason,
            "considered": record.considered_models_json,
        },
    }


@router.post("/{dataset_id}/predict", response_model=RepredictResponse)
def repredict(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_WRITE)),
    db: Session = Depends(get_db),
):
    """Re-run model selection + prediction against a freshly reloaded
    registry — see services/dataset_intelligence/pipeline.repredict_dataset
    for the full rationale. Requires DATASETS_WRITE (not just READ) since
    this mutates stored analysis/prediction records, unlike every other
    GET endpoint in this router."""
    try:
        dataset, analysis = repredict_dataset(db, dataset_id)
    except DatasetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    except DatasetIntelligenceError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": exc.message, "stage": exc.stage, "issues": exc.issues},
        )

    return RepredictResponse(
        dataset_id=dataset.id, status=dataset.status,
        model_selection=ModelSelectionOut(
            selected_model_name=analysis.model_selection.selected_model.name
                if analysis.model_selection.selected_model else None,
            selected_model_version=analysis.model_selection.selected_model.version
                if analysis.model_selection.selected_model else None,
            schema_match_pct=analysis.model_selection.selected_match.match_pct
                if analysis.model_selection.selected_match else (
                    analysis.model_selection.considered[0].match_pct
                    if analysis.model_selection.considered else None
                ),
            fallback_reason=analysis.model_selection.fallback_reason,
            considered=[
                SchemaMatchOut(model_name=m.model_name, match_pct=m.match_pct,
                               matched_required=m.matched_required, missing_required=m.missing_required,
                               matched_optional=m.matched_optional, dtype_mismatches=m.dtype_mismatches,
                               missing_data_violations=m.missing_data_violations)
                for m in analysis.model_selection.considered
            ],
        ),
        prediction_summary=PredictionSummaryOut(
            model_name=analysis.prediction.model_name, model_version=analysis.prediction.model_version,
            problem_type=analysis.prediction.problem_type, target_variable=analysis.prediction.target_variable,
            n_rows_predicted=analysis.prediction.n_rows_predicted,
            rows_dropped_missing_features=analysis.prediction.rows_dropped_missing_features,
            inference_time_seconds=analysis.prediction.inference_time_seconds,
            rows_per_second=analysis.prediction.rows_per_second,
            prediction_distribution=analysis.prediction.prediction_distribution,
            top_global_features=dict(list((analysis.explainability.global_importance
                                            if analysis.explainability else {}).items())[:5]),
        ) if analysis.prediction is not None else None,
    )


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    dataset_id: str,
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_WRITE)),
    db: Session = Depends(get_db),
):
    """Soft delete via deleted_at, not a hard DELETE — a hard delete of a
    dataset with an audit trail pointing at it would be a data-integrity
    gap (see IMPLEMENTATION_PLAN.md Segment 8). Every other route in this
    file checks deleted_at via _get_active_dataset, so a soft-deleted
    dataset becomes immediately unreachable everywhere, not just hidden
    from the list endpoint."""
    dataset = _get_active_dataset(db, dataset_id)
    dataset.deleted_at = datetime.now(timezone.utc)
    audit_event(db, "dataset.deleted", resource_type="dataset", resource_id=dataset.id)


@models_router.get("", response_model=list[ModelInfoOut])
def list_models(
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
):
    """Every registered model across all domains — lets a frontend show
    'here's what we can predict' before any upload happens. Not tenant-
    scoped data (the registry is a shared, filesystem-backed catalog, not
    per-tenant DB rows) — permission-gated the same as dataset reads
    since it's still dataset-intelligence-feature-flagged information."""
    registry = get_cached_registry(_settings.model_registry_dir)
    return [
        ModelInfoOut(
            name=m.name, domain=m.domain, version=m.version, problem_type=m.problem_type,
            target_variable=m.target_variable, accuracy=m.accuracy,
            artifact_available=m.artifact_available, description=m.description,
        )
        for m in registry
    ]


@models_router.get("/domains", response_model=list[DomainInfoOut])
def list_domains(
    tenant_ctx=Depends(get_tenant_context),
    _claims: dict = Depends(require_permission(Perm.DATASETS_READ)),
):
    """Full domain catalog for a 'supported domains' help page — static
    reference data from domain_knowledge.py, not a DB query."""
    return [
        DomainInfoOut(name=spec.name, example_signature_columns=list(spec.signature_columns[:5]))
        for spec in DOMAINS.values()
    ]
