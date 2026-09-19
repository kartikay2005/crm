"""
Dataset Intelligence Engine — Step 8: Model Repository.

Models live under `model_registry/<domain_slug>/<model_name>.joblib` with a
sidecar `<model_name>.json` describing everything the schema-matching and
selection logic needs. The registry is a pure filesystem scan — adding a
21st domain or a 2nd finance model is "drop two files in a folder," never a
code change (spec Step 26).

Metadata JSON shape (all fields required unless noted):
{
  "name": "Heart Disease Predictor",
  "version": "1.0.0",
  "domain": "Healthcare",
  "problem_type": "binary_classification",
  "target_variable": "diagnosis",
  "required_features": [
    {"name": "age", "dtype": "numerical"},
    {"name": "blood_pressure", "dtype": "numerical"},
    {"name": "heart_rate", "dtype": "numerical"},
    {"name": "cholesterol", "dtype": "numerical"}
  ],
  "optional_features": [{"name": "patient_id", "dtype": "numerical"}],
  "allowed_missing_pct": 10.0,
  "accuracy": 0.87,
  "training_date": "2026-01-15",
  "description": "Predicts heart disease risk from vitals."
}

The registry does NOT require the .joblib artifact to exist to be usable
for schema matching / model selection (Segment 3's scope) — only Segment 4
(actual prediction) needs to load the artifact. This means schema-matching
logic can be fully built and tested before every domain has a real trained
model behind it, and a metadata-only "coming soon" entry is a valid, safe
registry state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from core.logging_config import get_logger

_log = get_logger(__name__)

VALID_PROBLEM_TYPES = {
    "binary_classification", "multiclass_classification", "regression",
    "forecasting", "clustering", "anomaly_detection",
}


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str  # "numerical" | "categorical" | "datetime" | "boolean"


@dataclass(frozen=True)
class ModelMetadata:
    name: str
    version: str
    domain: str
    problem_type: str
    target_variable: str
    required_features: tuple[FeatureSpec, ...]
    optional_features: tuple[FeatureSpec, ...]
    allowed_missing_pct: float
    accuracy: float | None
    training_date: str | None
    description: str
    artifact_path: Path
    metadata_path: Path

    @property
    def artifact_available(self) -> bool:
        return self.artifact_path.exists()


class RegistryLoadError(Exception):
    """A single bad metadata file. Callers should log and skip, not crash
    the whole registry over one malformed entry."""


def _parse_metadata(json_path: Path) -> ModelMetadata:
    try:
        raw = json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise RegistryLoadError(f"{json_path}: {exc}") from exc

    missing = [k for k in ("name", "version", "domain", "problem_type", "target_variable",
                            "required_features", "allowed_missing_pct")
               if k not in raw]
    if missing:
        raise RegistryLoadError(f"{json_path}: missing required fields {missing}")

    if raw["problem_type"] not in VALID_PROBLEM_TYPES:
        raise RegistryLoadError(
            f"{json_path}: problem_type '{raw['problem_type']}' not in {VALID_PROBLEM_TYPES}"
        )

    artifact_path = json_path.with_suffix(".joblib")

    return ModelMetadata(
        name=raw["name"],
        version=raw["version"],
        domain=raw["domain"],
        problem_type=raw["problem_type"],
        target_variable=raw["target_variable"],
        required_features=tuple(FeatureSpec(**f) for f in raw["required_features"]),
        optional_features=tuple(FeatureSpec(**f) for f in raw.get("optional_features", [])),
        allowed_missing_pct=float(raw["allowed_missing_pct"]),
        accuracy=raw.get("accuracy"),
        training_date=raw.get("training_date"),
        description=raw.get("description", ""),
        artifact_path=artifact_path,
        metadata_path=json_path,
    )


def load_registry(root_dir: str | Path) -> list[ModelMetadata]:
    """Scan `root_dir/<domain>/*.json` and parse each as a ModelMetadata.
    A single malformed entry is logged and skipped, not fatal to the rest
    of the registry — one bad JSON file shouldn't take down model
    selection for every other domain."""
    root = Path(root_dir)
    if not root.exists():
        _log.warning("model_registry.root_missing", path=str(root))
        return []

    models: list[ModelMetadata] = []
    for json_path in sorted(root.glob("*/*.json")):
        try:
            models.append(_parse_metadata(json_path))
        except RegistryLoadError as exc:
            _log.error("model_registry.bad_entry", error=str(exc))
    _log.info("model_registry.loaded", count=len(models),
              with_artifact=sum(1 for m in models if m.artifact_available))
    return models


def get_models_for_domain(models: list[ModelMetadata], domain: str) -> list[ModelMetadata]:
    return [m for m in models if m.domain == domain]


@lru_cache
def get_cached_registry(root_dir: str) -> tuple[ModelMetadata, ...]:
    """Cached accessor so the registry isn't rescanned from disk on every
    upload. Returns a tuple (hashable, required for lru_cache) rather than
    a list. Cache is keyed on root_dir, so tests can bypass it entirely by
    calling load_registry() directly with a different path.

    NOTE: this cache is process-lifetime — if you edit registry files while
    the app is running, restart the process (or call reload_registry()
    below) to pick up the change."""
    return tuple(load_registry(root_dir))


def reload_registry(root_dir: str) -> tuple[ModelMetadata, ...]:
    """Force a fresh filesystem scan, bypassing the lru_cache. Used by
    `POST /datasets/{id}/predict` (Segment 8) — the whole point of that
    endpoint is to re-check whether a new/better model has been added to
    the registry since the original upload, so it must NOT see the stale
    cached result get_cached_registry would otherwise return.

    NOTE: `cache_clear()` clears the ENTIRE lru_cache, not just the entry
    for `root_dir` — lru_cache doesn't support per-key invalidation without
    extra bookkeeping. Acceptable here because this app only ever has one
    registry root in practice (from core.config.model_registry_dir); if
    that ever changes, this would need a proper per-key cache."""
    get_cached_registry.cache_clear()
    return get_cached_registry(root_dir)
