"""
Dataset Intelligence Engine — Steps 3-4: Domain Detection + Confidence Score.

Scores every known domain against the dataset's profiled columns using five
weighted components (weights per the spec):
  - column_similarity (35%)   — how many dataset columns match this domain's
                                  signature/keyword vocabulary
  - feature_names (20%)       — overlap with the domain's canonical
                                  signature_columns list
  - data_types (15%)          — how closely the numerical/categorical mix
                                  matches what's expected for this domain
  - distribution_similarity (10%) — for columns matching a known field with
                                  a curated plausible range (e.g. "age" ->
                                  0-120), what fraction of values fall in range
  - schema_completeness (10%) — fraction of signature_columns present
  - domain_keywords (10%)     — looser keyword overlap across all column
                                  names, catching partial/compound names

The result is intentionally explainable: every sub-score is returned
alongside the total, and the top few matched columns are recorded per
domain so the UI/report can show *why* a domain was chosen — matching the
project's broader "explainable over opaque" design principle.

This is a rule-based/statistical detector, not an ML classifier — no
training data, no black box. That's a deliberate scope choice for this
segment; nothing here prevents swapping in a learned classifier later
behind the same DomainDetectionResult interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from services.dataset_intelligence.domain_knowledge import DOMAINS, GENERAL_DOMAIN, DomainSpec
from services.dataset_intelligence.profiler import DatasetProfile
from services.dataset_intelligence.text_matching import matches, normalize as _normalize

_WEIGHTS = {
    "column_similarity": 0.35,
    "feature_names": 0.20,
    "data_types": 0.15,
    "distribution_similarity": 0.10,
    "schema_completeness": 0.10,
    "domain_keywords": 0.10,
}


@dataclass
class DomainScore:
    domain: str
    confidence: float  # 0-100
    band: str  # "green" | "yellow" | "red"
    component_scores: dict[str, float]
    matched_columns: list[str] = field(default_factory=list)


@dataclass
class DomainDetectionResult:
    top_domain: str
    top_confidence: float
    band: str
    all_scores: list[DomainScore]  # sorted descending by confidence


def _confidence_band(confidence: float) -> str:
    if confidence >= 80:
        return "green"
    if confidence >= 50:
        return "yellow"
    return "red"


def _column_similarity_score(norm_columns: list[str], spec: DomainSpec) -> tuple[float, list[str]]:
    vocab = spec.signature_columns + spec.keywords
    matched = []
    for col in norm_columns:
        # Word-boundary matching (see text_matching.py) — plain substring
        # containment let short keywords like "age" falsely match inside
        # unrelated words that happen to contain them as a substring (e.g.
        # "age" is contiguous inside "average" — "average_revenue" would
        # wrongly count toward Healthcare's score without word boundaries).
        if any(matches(term, col) for term in vocab):
            matched.append(col)
    score = 100 * len(matched) / max(len(norm_columns), 1)
    return min(score, 100.0), matched


def _feature_names_score(norm_columns: list[str], spec: DomainSpec) -> float:
    if not spec.signature_columns:
        return 0.0
    hits = sum(
        1 for sig in spec.signature_columns
        if any(matches(sig, col) for col in norm_columns)
    )
    return 100 * hits / len(spec.signature_columns)


def _data_types_score(profile: DatasetProfile, spec: DomainSpec) -> float:
    total = profile.n_columns or 1
    actual_ratio = len(profile.numerical_columns) / total
    diff = abs(actual_ratio - spec.expected_numerical_ratio)
    return max(0.0, 100 * (1 - diff / 0.6))  # full credit at diff=0, 0 credit at diff>=0.6


def _distribution_similarity_score(df: pd.DataFrame, norm_col_map: dict[str, str],
                                    spec: DomainSpec) -> float:
    if not spec.known_ranges:
        return 50.0  # neutral — no curated ranges to check for this domain
    checked, total_in_range_frac = 0, 0.0
    for field_name, (lo, hi) in spec.known_ranges.items():
        for norm, original in norm_col_map.items():
            if matches(field_name, norm):
                series = pd.to_numeric(df[original], errors="coerce").dropna()
                if series.empty:
                    continue
                in_range = float(((series >= lo) & (series <= hi)).mean())
                total_in_range_frac += in_range
                checked += 1
                break
    if checked == 0:
        return 50.0  # neutral — domain has ranges, but none of its fields are present
    return float(100 * total_in_range_frac / checked)


def _schema_completeness_score(norm_columns: list[str], spec: DomainSpec) -> float:
    if not spec.signature_columns:
        return 0.0
    present = sum(
        1 for sig in spec.signature_columns
        if any(matches(sig, col) for col in norm_columns)
    )
    return 100 * present / len(spec.signature_columns)


def _domain_keywords_score(all_columns_text: str, spec: DomainSpec) -> float:
    if not spec.keywords:
        return 0.0
    hits = sum(1 for kw in spec.keywords if matches(kw, all_columns_text))
    return min(100.0, 100 * hits / max(len(spec.keywords) * 0.4, 1))  # matching 40%+ of keywords = full credit


def detect_domain(df: pd.DataFrame, profile: DatasetProfile) -> DomainDetectionResult:
    norm_col_map = {_normalize(c): c for c in df.columns}
    norm_columns = list(norm_col_map.keys())
    all_columns_text = " | ".join(norm_columns)

    scores: list[DomainScore] = []
    for spec in DOMAINS.values():
        col_sim, matched = _column_similarity_score(norm_columns, spec)
        feat_names = _feature_names_score(norm_columns, spec)
        dtypes = _data_types_score(profile, spec)
        dist = _distribution_similarity_score(df, norm_col_map, spec)
        schema = _schema_completeness_score(norm_columns, spec)
        keywords = _domain_keywords_score(all_columns_text, spec)

        components = {
            "column_similarity": round(col_sim, 1),
            "feature_names": round(feat_names, 1),
            "data_types": round(dtypes, 1),
            "distribution_similarity": round(dist, 1),
            "schema_completeness": round(schema, 1),
            "domain_keywords": round(keywords, 1),
        }
        total = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS)
        scores.append(DomainScore(
            domain=spec.name, confidence=round(total, 1), band=_confidence_band(total),
            component_scores=components,
            matched_columns=[norm_col_map[c] for c in matched][:10],
        ))

    scores.sort(key=lambda s: s.confidence, reverse=True)

    # Fallback: if even the best domain scores low, call it General rather
    # than force-fitting a misleading label (spec: "should NOT rely only on
    # file names" and implies graceful degradation when nothing fits well).
    top = scores[0]
    if top.confidence < 35.0:
        general = DomainScore(
            domain=GENERAL_DOMAIN, confidence=50.0, band="yellow",
            component_scores={}, matched_columns=[],
        )
        # Keep the real per-domain scores visible too (useful for the UI to
        # show "closest matches were X, Y, Z, none confident enough"), but
        # report General as the top-level result.
        return DomainDetectionResult(
            top_domain=GENERAL_DOMAIN, top_confidence=general.confidence,
            band=general.band, all_scores=[general] + scores,
        )

    return DomainDetectionResult(
        top_domain=top.domain, top_confidence=top.confidence, band=top.band, all_scores=scores,
    )
