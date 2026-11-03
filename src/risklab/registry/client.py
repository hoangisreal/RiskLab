"""MLflow registry client helpers: pinned-version resolution and alias moves."""

from __future__ import annotations

import os
from typing import Any

MODEL_NAME = "risklab-credit-risk"


class RegistryError(Exception):
    """Raised when the registry cannot be reached or the alias is missing."""


def _client() -> Any:
    import mlflow
    from mlflow.tracking import MlflowClient

    uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if not uri:
        raise RegistryError("MLFLOW_TRACKING_URI is not set")
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
    }


def set_alias(version: str, alias: str = "champion") -> None:
    _client().set_registered_model_alias(MODEL_NAME, alias, version)


def delete_alias(alias: str = "champion") -> None:
    _client().delete_registered_model_alias(MODEL_NAME, alias)


__all__ = ["MODEL_NAME", "RegistryError", "delete_alias", "resolve_champion", "set_alias"]
