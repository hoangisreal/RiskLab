"""MLflow registry client helpers: pinned-version resolution and alias moves."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from risklab.manifest import sha256_file
from risklab.monitoring.profile import ReferenceProfile
from risklab.registry.bundle import ServingConfig, validate_serving_config
from risklab.registry.gates import promotion_gate

MODEL_NAME = "risklab-credit-risk"


class RegistryError(Exception):
    """Raised when the registry cannot be reached or the alias is missing."""


def _client() -> Any:
    import mlflow
    from mlflow.tracking import MlflowClient

    uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if not uri:
        raise RegistryError("MLFLOW_TRACKING_URI is not set")
    if not uri.startswith(("http://", "https://")):
        raise RegistryError("MLFLOW_TRACKING_URI must point at an HTTP tracking server")
    mlflow.set_tracking_uri(uri)
    return MlflowClient(tracking_uri=uri)


def resolve_champion() -> dict[str, Any]:
    """Resolve ``@champion`` to a pinned version (metadata + model cannot diverge)."""
    client = _client()
    try:
        mv = client.get_model_version_by_alias(MODEL_NAME, "champion")
    except Exception as exc:
        raise RegistryError(f"cannot resolve champion alias: {exc}") from exc
    return {
        "name": MODEL_NAME,
        "version": str(mv.version),
        "run_id": mv.run_id,
        "tags": dict(mv.tags or {}),
        "registered_at_ms": mv.creation_timestamp,
    }


def set_alias(version: str, alias: str = "champion") -> None:
    try:
        _client().set_registered_model_alias(MODEL_NAME, alias, version)
    except Exception as exc:
        raise RegistryError(f"cannot change alias: {exc}") from exc


def champion_info_or_none() -> dict[str, Any] | None:
    """Distinguish a missing alias from registry/network errors without error-text matching."""
    try:
        registered = _client().get_registered_model(MODEL_NAME)
        version = (registered.aliases or {}).get("champion")
        return resolve_version(str(version)) if version is not None else None
    except Exception as exc:
        raise RegistryError(f"cannot inspect champion alias: {exc}") from exc


def resolve_version(version: str) -> dict[str, Any]:
    if not version.isdigit():
        raise RegistryError("model version must be a numeric registry version")
    try:
        mv = _client().get_model_version(MODEL_NAME, version)
    except Exception as exc:
        raise RegistryError(f"cannot resolve version {version}: {exc}") from exc
    return {
        "name": MODEL_NAME,
        "version": str(mv.version),
        "run_id": mv.run_id,
        "tags": dict(mv.tags or {}),
        "registered_at_ms": mv.creation_timestamp,
    }


def read_serving_config(info: dict[str, Any]) -> ServingConfig:
    import mlflow.artifacts

    if not info.get("run_id"):
        raise RegistryError("registered model has no run id")
    try:
        path = mlflow.artifacts.download_artifacts(
            artifact_uri=f"runs:/{info['run_id']}/serving_config.json"
        )
        config = ServingConfig.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        tags = info.get("tags") or {}
        for key in ("lock_id", "contract_version", "model_sha256"):
            if tags.get(key) != getattr(config, key):
                raise ValueError(f"registry tag {key} differs from serving_config")
        return config
    except Exception as exc:
        raise RegistryError(f"invalid serving bundle: {exc}") from exc


def read_reference_profile(info: dict[str, Any], config: ServingConfig) -> ReferenceProfile:
    import mlflow.artifacts

    path = Path(
        mlflow.artifacts.download_artifacts(
            artifact_uri=f"runs:/{info['run_id']}/reference_profile.json"
        )
    )
    if sha256_file(path) != config.reference_profile_sha256:
        raise RegistryError("reference profile hash differs from serving_config")
    profile = ReferenceProfile.model_validate(json.loads(path.read_text()))
    if (
        profile.model_sha256 != config.model_sha256
        or profile.feature_columns != config.feature_columns
        or profile.contract_version != config.contract_version
        or profile.selection_metrics != config.selection_metrics
    ):
        raise RegistryError("reference profile differs from the pinned model contract")
    return profile


def check_promotion(
    version: str,
    *,
    expected_contract: str,
    config: dict[str, float] | None = None,
    expected_protocol: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Re-evaluate the gate against the current champion, before changing any alias."""
    try:
        from risklab.api.model_store import ModelStore, load_from_registry

        candidate_info = resolve_version(version)
        candidate = read_serving_config(candidate_info)
        validate_serving_config(candidate, expected_contract=expected_contract)
        load_from_registry(
            ModelStore(),
            version=version,
            expected_contract=expected_contract,
            expected_protocol=expected_protocol,
        )
        champion = None
        previous = None
        current = champion_info_or_none()
        if current is not None:
            previous = current["version"]
            metrics = _client().get_run(current["run_id"]).data.metrics
            required = ("roc_auc", "log_loss", "brier")
            if not all(f"selection_{key}" in metrics for key in required):
                raise RegistryError("champion lacks recorded Selection metrics")
            champion = {key: float(metrics[f"selection_{key}"]) for key in required}
        gate = promotion_gate(
            candidate.selection_metrics,
            champion,
            ece=candidate.selection_ece,
            contract_valid=True,
            parity_passed=candidate.parity["passed"],
            versions_match=True,
            lock_present=bool(candidate.lock_id),
            config=config,
        )
        gate.update(champion_version=previous, model_sha256=candidate.model_sha256)
        _client().log_dict(candidate_info["run_id"], gate, "gate_report.json")
        _client().set_model_version_tag(
            MODEL_NAME, version, "gate_status", "passed" if gate["passed"] else "blocked"
        )
        return gate
    except (ValueError, KeyError) as exc:
        raise RegistryError(f"candidate validation failed: {exc}") from exc


def delete_alias(alias: str = "champion") -> None:
    _client().delete_registered_model_alias(MODEL_NAME, alias)


__all__ = ["MODEL_NAME", "RegistryError", "delete_alias", "resolve_champion", "set_alias"]
