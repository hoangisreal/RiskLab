"""Pinned ML serving adapter for the worker: verified bundle, shared inference.

The adapter reuses the legacy serving path (cleaning, shared SQL features,
calibrated prediction, log-odds explanation) so batch and worker scoring stay
identical. It never derives a human decision from the model recommendation.
"""

from __future__ import annotations

import hashlib
import pickle  # noqa: S403 - operator-pinned trusted local artifact only
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from risklab.api.model_store import LoadedModel, ModelStore
from risklab.api.schemas import PredictRequest
from risklab.api.service import ServiceError, predict
from risklab.business.catalog import PinnedRelease
from risklab.business.errors import AppError
from risklab.features.registry import load_registry
from risklab.manifest import config_hash


@dataclass(frozen=True)
class ScoreResult:
    probability: float
    threshold: float
    sources_used: list[str]
    warnings: list[str]
    explanation: dict[str, Any]
    feature_schema_hash: str


class PinnedScoringModel:
    """Loads one operator-pinned bundle after integrity and contract checks."""

    def __init__(
        self,
        *,
        model_path: Path,
        release: PinnedRelease,
        config_dir: Path,
        root: Path,
    ) -> None:
        if (
            release.threshold is None
            or not release.policy_version
            or not release.protocol_hashes
            or not release.feature_columns
        ):
            raise AppError(503, "MODEL_UNAVAILABLE", "Pinned release is incomplete for scoring")
        data = model_path.read_bytes() if model_path.is_file() else b""
        if hashlib.sha256(data).hexdigest() != release.model_sha256:
            raise AppError(503, "MODEL_UNAVAILABLE", "Model artifact integrity check failed")
        registry = load_registry(config_dir)
        if release.feature_contract_version != registry.contract_version:
            raise AppError(503, "MODEL_UNAVAILABLE", "Model feature contract is incompatible")
        model = pickle.loads(data)  # noqa: S301 - hash-verified above
        names = getattr(model, "feature_names_in_", None)
        if names is None or list(names) != list(release.feature_columns):
            raise AppError(503, "MODEL_UNAVAILABLE", "Model input columns differ from release")
        loaded = LoadedModel(
            version=release.model_version,
            model=model,
            threshold=release.threshold,
            contract_version=release.feature_contract_version,
            feature_groups=list(release.feature_groups),
            run_id=release.model_run_id,
            lock_id=release.lock_id,
        )
        store = ModelStore()
        store.swap(loaded)
        self.loaded_model = loaded
        self._store = store
        self._registry = registry
        self.config_dir = config_dir
        self.root = root
        self.release = release
        self.feature_schema_hash = config_hash(
            {
                "contract_version": release.feature_contract_version,
                "feature_groups": list(release.feature_groups),
                "feature_columns": list(release.feature_columns),
            }
        )

    def score(self, raw_payload: dict[str, Any]) -> ScoreResult:
        """Score one validated snapshot payload; ServiceError keeps its status."""
        request = PredictRequest.model_validate(
            {
                "contract_version": raw_payload.get("contract_version"),
                "application": raw_payload.get("application", {}),
                "history": raw_payload.get("history", {}),
                "explain": True,
            }
        )
        response = predict(
            request,
            store=self._store,
            registry=self._registry,
            root=self.root,
            config_dir=self.config_dir,
        )
        if response.explanation is None:
            raise ServiceError("explanation is required for persisted predictions", status=503)
        return ScoreResult(
            probability=response.probability,
            threshold=response.threshold,
            sources_used=response.sources_used,
            warnings=response.warnings,
            explanation=response.explanation.model_dump(),
            feature_schema_hash=self.feature_schema_hash,
        )
