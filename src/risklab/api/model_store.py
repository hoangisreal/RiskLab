"""Model store: pinned version, validation before serving, atomic reload."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LoadedModel:
    version: str
    model: Any
    threshold: float
    contract_version: str
    feature_groups: list[str]
    run_id: str | None = None
    lock_id: str | None = None


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
        expected_contract: str = "1.0",
    ) -> LoadedModel:
        import pickle  # noqa: S403 - trusted local artifact

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


def load_from_registry(store: ModelStore) -> LoadedModel:
    """Resolve @champion to a pinned version and load it (startup path)."""
    import mlflow
    import mlflow.sklearn

    from risklab.registry.client import MODEL_NAME, resolve_champion

    info = resolve_champion()
    version = str(info["version"])
    tags = dict(info.get("tags") or {})
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}/{version}")
    loaded = LoadedModel(
        version=version,
        model=model,
        threshold=float(tags.get("threshold", 0.1)),
        contract_version=str(tags.get("contract_version", "1.0")),
        feature_groups=[g for g in str(tags.get("feature_groups", "")).split(",") if g],
        run_id=info.get("run_id"),
        lock_id=tags.get("lock_id"),
    )
    store.swap(loaded)
    return loaded
