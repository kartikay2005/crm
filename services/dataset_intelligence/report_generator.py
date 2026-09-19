"""
Dataset Intelligence Engine — Step 17: Report Export.

`assemble_report_data` is the single source of truth every format renders
from — PDF, Excel, CSV, and JSON all pull from the same flat dict rather
than each independently reaching into DB records, so the four formats
can't silently drift from each other.

Every generator takes already-persisted data (profile/analysis/prediction/
insight records, already loaded by the API route) rather than re-running
any part of the pipeline — report generation should never trigger a
re-analysis, re-prediction, or re-read of the source file.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


class ReportGenerationError(Exception):
    """Friendly, typed report failures."""


def assemble_report_data(
    dataset, profile_record, analysis_record, prediction_record, insight_record,
) -> dict:
    """One flat dict every format renders from. `prediction_record` and
    should-be-optional fields may be None (fallback path) — every
    generator below must handle that gracefully, not assume a prediction
    always exists."""
    return {
        "dataset_name": dataset.original_filename,
        "dataset_id": dataset.id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_rows": profile_record.profile_json.get("n_rows"),
        "n_columns": profile_record.profile_json.get("n_columns"),
        "top_domain": analysis_record.top_domain,
        "domain_confidence": analysis_record.domain_confidence,
        "domain_band": analysis_record.domain_band,
        "quality_score": analysis_record.quality_score,
        "quality_label": analysis_record.quality_label,
        "readiness_score": analysis_record.readiness_score,
        "readiness_label": analysis_record.readiness_label,
        "selected_model_name": analysis_record.selected_model_name,
        "selected_model_version": analysis_record.selected_model_version,
        "schema_match_pct": analysis_record.schema_match_pct,
        "fallback_reason": analysis_record.fallback_reason,
        "prediction_available": prediction_record is not None,
        "prediction_summary": (
            {
                "target_variable": prediction_record.target_variable,
                "problem_type": prediction_record.problem_type,
                "n_rows_predicted": prediction_record.n_rows_predicted,
                "distribution": prediction_record.prediction_distribution_json,
                "top_features": dict(list(prediction_record.global_importance_json.items())[:5]),
            }
            if prediction_record is not None else None
        ),
        "insights_summary": insight_record.summary if insight_record else "",
        "top_insights": (
            insight_record.insights_json.get("key_findings", [])[:5] if insight_record else []
        ),
        "recommendations": insight_record.recommendations_json if insight_record else [],
    }


# ---------------------------------------------------------------------------
# PDF — executive summary, 2-4 pages, not the full profile dump
# ---------------------------------------------------------------------------
def generate_pdf_report(report_data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCustom", parent=styles["Title"], spaceAfter=6)
    h2 = styles["Heading2"]
    body = styles["BodyText"]

    story = []
    story.append(Paragraph("Dataset Intelligence Report", title_style))
    story.append(Paragraph(report_data["dataset_name"], styles["Heading3"]))
    story.append(Paragraph(f"Generated {report_data['generated_at']}", body))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Summary", h2))
    summary_table_data = [
        ["Rows", str(report_data["n_rows"])],
        ["Columns", str(report_data["n_columns"])],
        ["Detected Domain", f"{report_data['top_domain']} ({report_data['domain_confidence']:.1f}%, {report_data['domain_band']})"],
        ["Data Quality", f"{report_data['quality_score']:.1f}/100 ({report_data['quality_label']})"],
        ["AI Readiness", f"{report_data['readiness_score']:.1f}/100 ({report_data['readiness_label']})"],
    ]
    t = Table(summary_table_data, colWidths=[2 * inch, 4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Prediction", h2))
    if report_data["prediction_available"]:
        p = report_data["prediction_summary"]
        story.append(Paragraph(
            f"Model: {report_data['selected_model_name']} v{report_data['selected_model_version']} "
            f"(schema match {report_data['schema_match_pct']:.1f}%)", body,
        ))
        story.append(Paragraph(
            f"Predicted {p['target_variable']} for {p['n_rows_predicted']} rows.", body,
        ))
        if p["top_features"]:
            feat_rows = [["Feature", "Importance"]] + [
                [k, f"{v:.3f}"] for k, v in p["top_features"].items()
            ]
            ft = Table(feat_rows, colWidths=[3 * inch, 2 * inch])
            ft.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]))
            story.append(Spacer(1, 0.1 * inch))
            story.append(ft)
    else:
        story.append(Paragraph(
            report_data["fallback_reason"] or
            "No pretrained model was confidently selected for this dataset — only "
            "exploratory insights are shown below.", body,
        ))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Key Insights", h2))
    if report_data["top_insights"]:
        for insight in report_data["top_insights"]:
            story.append(Paragraph(f"• {insight['text']}", body))
    else:
        story.append(Paragraph("No notable insights were flagged for this dataset.", body))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Recommendations", h2))
    if report_data["recommendations"]:
        for rec in report_data["recommendations"]:
            story.append(Paragraph(f"• [{rec['priority'].upper()}] {rec['text']}", body))
    else:
        story.append(Paragraph("No domain-specific recommendations were generated for this dataset.", body))

    try:
        doc.build(story)
    except Exception as exc:  # noqa: BLE001
        raise ReportGenerationError(f"Failed to build PDF report: {exc}") from exc
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Excel — multi-sheet workbook
# ---------------------------------------------------------------------------
def generate_excel_report(
    report_data: dict, full_profile: dict, full_predictions: dict | None,
) -> bytes:
    wb = Workbook()

    # --- Summary sheet ---
    ws = wb.active
    ws.title = "Summary"
    bold = Font(bold=True)
    rows = [
        ("Dataset", report_data["dataset_name"]),
        ("Generated", report_data["generated_at"]),
        ("Rows", report_data["n_rows"]),
        ("Columns", report_data["n_columns"]),
        ("Detected Domain", f"{report_data['top_domain']} ({report_data['domain_confidence']:.1f}%)"),
        ("Data Quality", f"{report_data['quality_score']:.1f}/100 ({report_data['quality_label']})"),
        ("AI Readiness", f"{report_data['readiness_score']:.1f}/100 ({report_data['readiness_label']})"),
        ("Selected Model", report_data["selected_model_name"] or "None (see fallback reason)"),
        ("Fallback Reason", report_data["fallback_reason"] or ""),
    ]
    for r, (label, value) in enumerate(rows, start=1):
        ws.cell(row=r, column=1, value=label).font = bold
        ws.cell(row=r, column=2, value=value)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 60

    # --- Column Profile sheet ---
    ws2 = wb.create_sheet("Column Profile")
    headers = ["Column", "Type", "Missing %", "Unique", "Mean", "Min", "Max", "Outlier %"]
    for c, h in enumerate(headers, start=1):
        cell = ws2.cell(row=1, column=c, value=h)
        cell.font = bold
    for r, col in enumerate(full_profile.get("columns", []), start=2):
        ws2.cell(row=r, column=1, value=col.get("name"))
        ws2.cell(row=r, column=2, value=col.get("inferred_type"))
        ws2.cell(row=r, column=3, value=col.get("missing_pct"))
        ws2.cell(row=r, column=4, value=col.get("unique_count"))
        ws2.cell(row=r, column=5, value=col.get("mean"))
        ws2.cell(row=r, column=6, value=col.get("min"))
        ws2.cell(row=r, column=7, value=col.get("max"))
        ws2.cell(row=r, column=8, value=col.get("outlier_pct"))

    # --- Predictions sheet ---
    ws3 = wb.create_sheet("Predictions")
    if full_predictions and full_predictions.get("row_predictions"):
        headers = ["Row Index", "Prediction", "Probability"]
        for c, h in enumerate(headers, start=1):
            ws3.cell(row=1, column=c, value=h).font = bold
        for r, row in enumerate(full_predictions["row_predictions"], start=2):
            ws3.cell(row=r, column=1, value=row.get("row_index"))
            ws3.cell(row=r, column=2, value=row.get("prediction"))
            ws3.cell(row=r, column=3, value=row.get("probability"))
    else:
        ws3.cell(row=1, column=1, value="No prediction was made for this dataset.")

    # --- Insights & Recommendations sheet ---
    ws4 = wb.create_sheet("Insights & Recommendations")
    ws4.cell(row=1, column=1, value="Insights").font = bold
    r = 2
    for insight in report_data["top_insights"]:
        ws4.cell(row=r, column=1, value=insight.get("text"))
        r += 1
    r += 1
    ws4.cell(row=r, column=1, value="Recommendations").font = bold
    r += 1
    for rec in report_data["recommendations"]:
        ws4.cell(row=r, column=1, value=f"[{rec['priority']}] {rec['text']}")
        r += 1
    ws4.column_dimensions["A"].width = 90

    buf = io.BytesIO()
    try:
        wb.save(buf)
    except Exception as exc:  # noqa: BLE001
        raise ReportGenerationError(f"Failed to build Excel report: {exc}") from exc
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CSV — row-level predictions only. Explicit error rather than an empty
# file when there's nothing to export (see IMPLEMENTATION_PLAN.md Segment 7
# — a CSV of nothing is confusing, not helpful).
# ---------------------------------------------------------------------------
def generate_csv_export(full_predictions: dict | None) -> bytes:
    if not full_predictions or not full_predictions.get("row_predictions"):
        raise ReportGenerationError(
            "No prediction data is available for this dataset to export as CSV."
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["row_index", "prediction", "probability"])
    for row in full_predictions["row_predictions"]:
        writer.writerow([row.get("row_index"), row.get("prediction"), row.get("probability")])
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# JSON — everything, pretty-printed
# ---------------------------------------------------------------------------
def generate_json_export(
    report_data: dict, full_profile: dict, full_analysis: dict,
    full_predictions: dict | None, full_insights: dict,
) -> bytes:
    payload = {
        "summary": report_data,
        "profile": full_profile,
        "analysis": full_analysis,
        "predictions": full_predictions,
        "insights": full_insights,
    }
    return json.dumps(payload, indent=2, default=str).encode("utf-8")
