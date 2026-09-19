"""
Dataset Intelligence Engine — unified reader for CSV/XLSX/XLS/TSV/JSON, plus
light, non-destructive cleaning before profiling.

Design choice: cleaning here is intentionally conservative (trim whitespace,
dedupe headers, coerce obvious numeric/datetime strings) — it does NOT drop
rows, impute missing values, or remove outliers. Those are domain/model-
specific decisions that belong to later pipeline stages (schema matching,
prediction), not to a generic intake step. Being destructive here would
silently corrupt the profile step's job of reporting what the data actually
looks like.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO

import pandas as pd

from core.logging_config import get_logger
from services.dataset_intelligence.validation import ValidationResult

_log = get_logger(__name__)

# Above this raw byte size, CSV/TSV reads switch to chunked parsing to
# bound peak memory during the read step itself. This does NOT make
# profiling/prediction streaming (that's a bigger redesign, out of scope
# here per IMPLEMENTATION_PLAN.md Segment 10) — it only prevents
# pandas.read_csv from needing to materialize the entire raw text buffer
# plus the entire parsed DataFrame simultaneously for large files, which
# is where a naive single-shot read wastes the most memory.
CHUNKED_READ_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB
CHUNK_SIZE_ROWS = 50_000


class DatasetReadError(Exception):
    """Raised when a file passed Step-1 validation but still can't be parsed
    into a DataFrame (e.g. a corrupt Excel workbook only openpyxl can detect).
    Always carries a friendly, non-technical message."""


@dataclass
class ReadResult:
    df: pd.DataFrame
    cleaning_notes: list[str]


def _read_csv_chunked(buf: BytesIO, encoding: str, sep: str) -> pd.DataFrame:
    """Read via chunksize and concatenate. Still produces one in-memory
    DataFrame at the end (true streaming profiling/prediction is a larger
    redesign than this segment's scope) but avoids pandas' single-shot
    parser needing to hold multiple intermediate full-size buffers at once
    for very large files."""
    chunks = pd.read_csv(buf, encoding=encoding, sep=sep, low_memory=False, chunksize=CHUNK_SIZE_ROWS)
    return pd.concat(chunks, ignore_index=True)


def read_dataset(filename: str, content: bytes, validation: ValidationResult) -> pd.DataFrame:
    """Parse raw bytes into a DataFrame using the format implied by
    `validation` (which already sniffed encoding/delimiter for text formats)."""
    ext = validation.extension
    use_chunked = len(content) > CHUNKED_READ_THRESHOLD_BYTES
    if use_chunked:
        _log.info("reader.chunked_read", filename=filename, size_bytes=len(content))
    try:
        if ext == ".csv":
            encoding = validation.detected_encoding or "utf-8"
            sep = validation.detected_delimiter or ","
            df = (
                _read_csv_chunked(BytesIO(content), encoding, sep) if use_chunked
                else pd.read_csv(BytesIO(content), encoding=encoding, sep=sep, low_memory=False)
            )
        elif ext == ".tsv":
            encoding = validation.detected_encoding or "utf-8"
            df = (
                _read_csv_chunked(BytesIO(content), encoding, "\t") if use_chunked
                else pd.read_csv(BytesIO(content), encoding=encoding, sep="\t", low_memory=False)
            )
        elif ext == ".json":
            text = content.decode(validation.detected_encoding or "utf-8")
            parsed = json.loads(text)
            df = pd.DataFrame(parsed) if isinstance(parsed, list) else pd.DataFrame(parsed)
        elif ext == ".xlsx":
            df = pd.read_excel(BytesIO(content), engine="openpyxl")
        elif ext == ".xls":
            df = pd.read_excel(BytesIO(content), engine="xlrd")
        else:
            raise DatasetReadError(f"Unsupported extension '{ext}' reached the reader — "
                                    f"this should have been caught by validation.")
    except DatasetReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatasetReadError(
            f"The file passed initial checks but could not be read as a "
            f"{ext} file. It may be corrupted, password-protected, or use "
            f"an unsupported internal format. ({type(exc).__name__})"
        ) from exc

    if df.empty:
        raise DatasetReadError("The file was read successfully but contains no data rows.")

    return df


def clean_dataset(df: pd.DataFrame) -> ReadResult:
    """Conservative, non-destructive cleaning — see module docstring."""
    notes: list[str] = []
    df = df.copy()

    # 1. Strip whitespace from column names
    original_cols = list(df.columns)
    df.columns = [str(c).strip() for c in df.columns]
    if list(df.columns) != original_cols:
        notes.append("Trimmed leading/trailing whitespace from column names.")

    # 2. De-duplicate column names (col, col_2, col_3, ...)
    # Track the full set of names already assigned (not just a per-original-
    # name counter) so a generated "col_2" can't silently collide with a
    # distinct column that was already named "col_2" in the source file —
    # e.g. columns ["a", "a", "a_2"] must not produce two columns both
    # named "a_2". Each candidate suffix is checked against everything
    # assigned so far, incrementing until it's actually unique.
    counts: dict[str, int] = {}
    used: set[str] = set()
    new_cols = []
    renamed = False
    for c in df.columns:
        name = c
        while name in used:
            counts[c] = counts.get(c, 1) + 1
            name = f"{c}_{counts[c]}"
            renamed = True
        used.add(name)
        new_cols.append(name)
    df.columns = new_cols
    if renamed:
        notes.append("Renamed duplicate column headers to keep them unique.")

    # 3. Strip whitespace from string cell values (object dtype columns only)
    obj_cols = df.select_dtypes(include="object").columns
    for col in obj_cols:
        mask = df[col].notna()
        df.loc[mask, col] = df.loc[mask, col].astype(str).str.strip()

    # 4. Best-effort type coercion: try numeric, then datetime, leave as
    #    string otherwise. Never forces a column that's mostly non-numeric.
    for col in obj_cols:
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        numeric_coerced = pd.to_numeric(non_null, errors="coerce")
        if numeric_coerced.notna().mean() >= 0.95:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            notes.append(f"Column '{col}' auto-converted to numeric.")
            continue
        sample = non_null.astype(str).head(20)
        looks_like_date = sample.str.match(
            r"^\d{4}-\d{2}-\d{2}|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
        ).mean() >= 0.7
        if looks_like_date:
            dt_coerced = pd.to_datetime(non_null, errors="coerce", format="mixed")
            if dt_coerced.notna().mean() >= 0.95:
                df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
                notes.append(f"Column '{col}' auto-converted to datetime.")

    return ReadResult(df=df, cleaning_notes=notes)
