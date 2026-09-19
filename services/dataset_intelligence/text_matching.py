"""
Shared column-name matching utility for the Dataset Intelligence Engine.

This exists because the same "does this canonical term refer to this
column name" logic was independently duplicated across domain_detection.py,
schema_matching.py, prediction.py, and recommendation_engine.py — and the
same bug class hit it twice as a result:

1. (Segment 5) Bidirectional substring containment let a short REAL column
   name (e.g. "income") falsely match inside an unrelated, longer
   CANONICAL search term (e.g. "debt to income") via the reverse
   direction. Fixed by restricting to one direction: term found within
   column name, never the reverse.

2. (Segment 10) Even after that fix, raw substring containment in the
   correct direction still has a residual problem: short canonical terms
   can coincidentally appear as a substring of an unrelated word. "age" is
   literally contained in "average" as characters, so a column named
   "average_revenue" would falsely match Healthcare's "age" signature term
   with no domain relationship between them whatsoever.

Both are the same root cause: naive substring containment doesn't respect
word boundaries. `matches()` below uses word-boundary regex matching
instead, handling both single-word terms ("age" won't match inside
"average" — no word boundary there) and multi-word terms ("debt ratio"
matches a column whose normalized name contains that exact phrase as
whole words).

Centralizing this in one place means the next bug fix in this family only
needs to happen once, not four times.
"""
from __future__ import annotations

import re
from functools import lru_cache


def normalize(name: str) -> str:
    """Lowercase, whitespace-normalized form used for all comparisons.
    Underscores/hyphens become spaces so 'blood_pressure' and
    'blood pressure' and 'Blood-Pressure' all normalize identically."""
    return re.sub(r"[_\-]+", " ", name.strip().lower())


@lru_cache(maxsize=4096)
def _compiled_pattern(term: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(term) + r"\b")


def matches(term: str, column_name: str) -> bool:
    """True if `term` (a canonical/vocabulary word or phrase) appears as
    whole word(s) within `column_name`. One-directional by design — see
    module docstring for why the reverse direction is never checked."""
    norm_term = normalize(term)
    norm_col = normalize(column_name)
    return bool(_compiled_pattern(norm_term).search(norm_col))


def find_column(candidates: list[str], columns: list[str]) -> str | None:
    """Returns the first column name (from `columns`, in order) whose
    normalized form contains any of `candidates` as whole word(s), or
    None. Exact-normalized-equality is checked first for precision before
    falling back to substring containment."""
    norm_candidates = [normalize(c) for c in candidates]
    for col in columns:
        if normalize(col) in norm_candidates:
            return col
    for col in columns:
        if any(matches(c, col) for c in candidates):
            return col
    return None
