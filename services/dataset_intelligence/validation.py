"""
Dataset Intelligence Engine — Step 1: File Validation.

Validates an uploaded file BEFORE any parsing is attempted. Every check
returns a structured, friendly ValidationIssue instead of raising — the
only exception this module raises is ValidationFailed, which the API layer
converts to a 422 with the full issue list. Nothing here should ever crash
with a raw traceback reaching the caller; unexpected errors are caught and
converted into a generic ValidationIssue so upload always degrades
gracefully.
"""
from __future__ import annotations

import csv as csv_module
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from core.config import get_settings
from core.logging_config import get_logger

_settings = get_settings()
_log = get_logger(__name__)


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"  # "error" | "warning"


@dataclass
class ValidationResult:
    ok: bool
    filename: str
    extension: str
    size_bytes: int
    detected_encoding: str | None = None
    detected_delimiter: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    def error_issues(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]


class ValidationFailed(Exception):
    def __init__(self, result: ValidationResult):
        self.result = result
        messages = "; ".join(i.message for i in result.error_issues())
        super().__init__(messages or "File validation failed")


_TEXT_ENCODINGS_TO_TRY = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def _sniff_encoding(raw: bytes) -> str | None:
    for enc in _TEXT_ENCODINGS_TO_TRY:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _sniff_delimiter(sample_text: str) -> str | None:
    try:
        dialect = csv_module.Sniffer().sniff(sample_text, delimiters=",;\t|")
        return dialect.delimiter
    except csv_module.Error:
        return None


def _check_duplicate_headers(header_row: list[str]) -> list[ValidationIssue]:
    issues = []
    seen: dict[str, int] = {}
    for h in header_row:
        key = h.strip().lower()
        seen[key] = seen.get(key, 0) + 1
    dupes = [k for k, count in seen.items() if count > 1 and k]
    if dupes:
        issues.append(ValidationIssue(
            code="duplicate_headers",
            message=f"Duplicate column names found: {', '.join(dupes)}. "
                    f"They'll be auto-renamed (e.g. 'revenue', 'revenue_2').",
            severity="warning",
        ))
    return issues


def validate_upload(filename: str, content: bytes) -> ValidationResult:
    """Run every Step-1 check against raw upload bytes. Never raises for
    expected validation failures — only for truly unexpected internal
    errors, and even those are caught and folded into the result."""
    issues: list[ValidationIssue] = []
    extension = Path(filename).suffix.lower()
    size_bytes = len(content)

    try:
        # --- Extension ---
        if extension not in _settings.dataset_allowed_extensions:
            issues.append(ValidationIssue(
                code="unsupported_extension",
                message=f"'{extension}' is not supported. Supported formats: "
                        f"{', '.join(_settings.dataset_allowed_extensions)}.",
            ))

        # --- Size ---
        max_bytes = _settings.dataset_max_upload_mb * 1024 * 1024
        if size_bytes == 0:
            issues.append(ValidationIssue(code="empty_file", message="The uploaded file is empty."))
        elif size_bytes > max_bytes:
            issues.append(ValidationIssue(
                code="file_too_large",
                message=f"File is {size_bytes / 1_048_576:.1f} MB, which exceeds the "
                        f"{_settings.dataset_max_upload_mb} MB limit.",
            ))

        # If we already have fatal issues, don't bother parsing further —
        # but still return a well-formed result rather than raising.
        if any(i.severity == "error" for i in issues):
            return ValidationResult(
                ok=False, filename=filename, extension=extension,
                size_bytes=size_bytes, issues=issues,
            )

        detected_encoding = None
        detected_delimiter = None

        if extension in (".csv", ".tsv"):
            detected_encoding = _sniff_encoding(content)
            if detected_encoding is None:
                issues.append(ValidationIssue(
                    code="unsupported_encoding",
                    message="Could not detect a supported text encoding "
                            "(tried UTF-8, UTF-8-BOM, Latin-1, CP1252). "
                            "The file may be corrupted or binary.",
                ))
            else:
                try:
                    text = content.decode(detected_encoding)
                    sample = text[:65536]
                    detected_delimiter = "\t" if extension == ".tsv" else _sniff_delimiter(sample)
                    if detected_delimiter is None:
                        issues.append(ValidationIssue(
                            code="invalid_delimiter",
                            message="Could not detect a consistent delimiter "
                                    "(tried comma, semicolon, tab, pipe).",
                        ))
                    else:
                        reader = csv_module.reader(io.StringIO(sample), delimiter=detected_delimiter)
                        header_row = next(reader, [])
                        if not header_row or all(not h.strip() for h in header_row):
                            issues.append(ValidationIssue(
                                code="missing_header",
                                message="No column headers could be detected in the first row.",
                            ))
                        else:
                            issues.extend(_check_duplicate_headers(header_row))
                except Exception:
                    issues.append(ValidationIssue(
                        code="corrupted_file",
                        message="The file could not be parsed as valid delimited text — "
                                "it may be corrupted or truncated.",
                    ))

        elif extension == ".json":
            detected_encoding = _sniff_encoding(content) or "utf-8"
            try:
                parsed = json.loads(content.decode(detected_encoding))
                if isinstance(parsed, list):
                    if not parsed:
                        issues.append(ValidationIssue(code="empty_file", message="The JSON array is empty."))
                    elif not isinstance(parsed[0], dict):
                        issues.append(ValidationIssue(
                            code="unsupported_json_shape",
                            message="Expected a JSON array of objects (records), e.g. "
                                    '[{"col1": 1, "col2": "a"}, ...].',
                        ))
                elif isinstance(parsed, dict):
                    pass  # column-oriented JSON — acceptable, handled by the reader
                else:
                    issues.append(ValidationIssue(
                        code="unsupported_json_shape",
                        message="Top-level JSON must be an array of records or an object of columns.",
                    ))
            except json.JSONDecodeError as e:
                issues.append(ValidationIssue(
                    code="corrupted_file",
                    message=f"Invalid JSON: {e.msg} at line {e.lineno}, column {e.colno}.",
                ))

        elif extension in (".xlsx", ".xls"):
            # Deep validation (corrupt workbook, no sheets) happens in the
            # reader step, since it requires openpyxl/xlrd to actually open
            # the file — duplicating that here would mean parsing twice.
            # We only do the cheap magic-byte sanity check.
            if extension == ".xlsx" and not content[:2] == b"PK":
                issues.append(ValidationIssue(
                    code="corrupted_file",
                    message="File has a .xlsx extension but doesn't look like a valid "
                            "Excel workbook (bad file signature).",
                ))

    except Exception as exc:  # noqa: BLE001 — intentional catch-all, see docstring
        # The user only ever sees the generic message below (never raw
        # internals), but without logging this server-side there'd be no
        # way to diagnose *why* validation broke for a given upload.
        _log.error("validation.unexpected_error", filename=filename, error=str(exc), exc_info=True)
        issues.append(ValidationIssue(
            code="internal_validation_error",
            message="An unexpected error occurred while validating the file. "
                    "Please check the file and try again.",
        ))

    ok = not any(i.severity == "error" for i in issues)
    return ValidationResult(
        ok=ok, filename=filename, extension=extension, size_bytes=size_bytes,
        detected_encoding=detected_encoding, detected_delimiter=detected_delimiter,
        issues=issues,
    )
