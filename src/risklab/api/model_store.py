"""Model store: pinned version, validation before serving, atomic reload."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from risklab.monitoring.profile import ReferenceProfile
from risklab.registry.bundle import ServingConfig, validate_serving_config


@dataclass
class LoadedModel:
    version: str
    model: Any
    threshold: float
    contract_version: str
    feature_groups: list[str]
    run_id: str | None = None
    lock_id: str | None = None
    serving_config: ServingConfig | None = None
    reference_profile: ReferenceProfile | None = None


class ModelStoreError(Exception):
    """Raised when no valid model can be loaded."""


@dataclass
class ModelStore:
    """Holds the served model; ``swap`` replaces the reference atomically."""

    _current: LoadedModel | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def current(self) -> LoadedModel | None:
        return self._current

    @property
    def ready(self) -> bool:
        return self._current is not None

    def swap(self, loaded: LoadedModel) -> None:
        with self._lock:
            self._current = loaded

    def load_local(
        self,
        path: Path,
        *,
        version: str,
        threshold: float,
        contract_version: str,
        feature_groups: list[str],
        expected_contract: str | None = None,
    ) -> LoadedModel:
        import pickle  # noqa: S403 - trusted local artifact

        if expected_contract is None:
            from risklab.features.registry import load_registry

            expected_contract = load_registry().contract_version
        if contract_version != expected_contract:
            raise ModelStoreError(
                "contract_version "
                f"{contract_version!r} does not match API contract {expected_contract!r}"
            )
        if not path.is_file():
            raise ModelStoreError(f"model artifact not found: {path}")
        with open(path, "rb") as fh:
            model = pickle.load(fh)  # noqa: S301
        loaded = LoadedModel(
            version=version,
            model=model,
            threshold=float(threshold),
            contract_version=contract_version,
            feature_groups=list(feature_groups),
        )
        self.swap(loaded)
        return loaded


__all__ = ["LoadedModel", "ModelStore", "ModelStoreError", "load_from_registry"]


def load_from_registry(
    store: ModelStore,
    *,
    version: str | None = None,
    expected_contract: str | None = None,
    expected_protocol: dict[str, str] | None = None,
) -> LoadedModel:
    """Resolve @champion to a pinned version and load it (startup path)."""
    import mlflow
    import mlflow.sklearn

    from risklab.registry.client import (
        MODEL_NAME,
        read_reference_profile,
        read_serving_config,
        resolve_champion,
        resolve_version,
    )

    if expected_contract is None:
        from risklab.features.registry import load_registry

        expected_contract = load_registry().contract_version
    info = resolve_version(version) if version is not None else resolve_champion()
    version = str(info["version"])
    config = read_serving_config(info)
    validate_serving_config(config, expected_contract=expected_contract)
    if expected_protocol is not None and config.protocol_hashes != expected_protocol:
        raise ModelStoreError("model feature/config code differs from this serving image")
    profile = read_reference_profile(info, config)
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}/{version}")
    validate_serving_config(config, expected_contract=expected_contract, model=model)
    loaded = LoadedModel(
        version=version,
        model=model,
        threshold=config.threshold,
        contract_version=config.contract_version,
        feature_groups=config.feature_groups,
        run_id=info.get("run_id"),
        lock_id=config.lock_id,
        serving_config=config,
        reference_profile=profile,
    )
    store.swap(loaded)
    return loaded
