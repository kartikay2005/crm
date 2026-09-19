"""
Dataset Intelligence Engine — Steps 9-10: Model Selection + Fallback Logic.

Picks the best-matching model for the detected domain, or explicitly
declines to pick one — the spec is emphatic on this point ("Never force
predictions") and that's enforced here as a hard threshold, not a
suggestion: below SCHEMA_MATCH_ACCEPT_THRESHOLD, `selected` is None and the
caller gets a plain-English reason, full stop. Segment 4 (prediction) must
respect `selected is None` as "insights only, no prediction" — it should
never be tempted to run a model anyway "just because one exists."
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.dataset_intelligence.domain_knowledge import GENERAL_DOMAIN
from services.dataset_intelligence.model_registry import ModelMetadata, get_models_for_domain
from services.dataset_intelligence.profiler import DatasetProfile
from services.dataset_intelligence.schema_matching import SchemaMatchResult, match_schema

# Per spec Step 10: "If confidence < 80%, do NOT use pretrained model."
SCHEMA_MATCH_ACCEPT_THRESHOLD = 80.0


@dataclass
class ModelSelectionResult:
    selected_model: ModelMetadata | None
    selected_match: SchemaMatchResult | None
    considered: list[SchemaMatchResult] = field(default_factory=list)
    fallback_reason: str | None = None


def select_model(
    domain: str, profile: DatasetProfile, registry: list[ModelMetadata],
) -> ModelSelectionResult:
    if domain == GENERAL_DOMAIN:
        return ModelSelectionResult(
            selected_model=None, selected_match=None, considered=[],
            fallback_reason=(
                "This dataset doesn't match a known domain closely enough to "
                "select a pretrained model. Only exploratory insights are shown."
            ),
        )

    candidates = get_models_for_domain(registry, domain)
    if not candidates:
        return ModelSelectionResult(
            selected_model=None, selected_match=None, considered=[],
            fallback_reason=(
                f"No pretrained models are registered yet for the '{domain}' domain. "
                f"Only exploratory insights are shown."
            ),
        )

    scored = [(m, match_schema(profile, m)) for m in candidates]
    scored.sort(key=lambda pair: pair[1].match_pct, reverse=True)
    considered = [match for _, match in scored]

    best_model, best_match = scored[0]

    if best_match.match_pct < SCHEMA_MATCH_ACCEPT_THRESHOLD:
        reason = (
            f"This dataset does not sufficiently match any available pretrained "
            f"model for '{domain}' (best match: '{best_model.name}' at "
            f"{best_match.match_pct:.1f}%, below the {SCHEMA_MATCH_ACCEPT_THRESHOLD:.0f}% "
            f"threshold). Predictions may be inaccurate, so only exploratory "
            f"insights are shown."
        )
        if best_match.missing_required:
            reason += f" Missing required columns: {', '.join(best_match.missing_required)}."
        return ModelSelectionResult(
            selected_model=None, selected_match=None, considered=considered, fallback_reason=reason,
        )

    if not best_model.artifact_available:
        # Schema matches well, but the actual trained artifact isn't present
        # on disk (e.g. metadata-only "coming soon" registry entry). This is
        # a real, distinct failure mode from "no domain match" — worth its
        # own message so a user isn't left thinking their data is the problem.
        return ModelSelectionResult(
            selected_model=None, selected_match=best_match, considered=considered,
            fallback_reason=(
                f"'{best_model.name}' matches this dataset's schema "
                f"({best_match.match_pct:.1f}%), but its trained model file isn't "
                f"available yet. Only exploratory insights are shown."
            ),
        )

    return ModelSelectionResult(
        selected_model=best_model, selected_match=best_match, considered=considered, fallback_reason=None,
    )
