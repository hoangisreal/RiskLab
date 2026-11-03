"""Selection lock (MASTER_PLAN Section 4.3): freeze a choice before Test is opened."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from risklab.manifest import config_hash, git_info, library_versions, sha256_file, write_json_atomic


class SelectionError(Exception):
    """Raised for invalid locks or failed lock verification."""


def _lock_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def create_lock(
    *,
    locks_dir: Path,
    model_run_id: str,
    feature_set: str,
    feature_groups: list[str],
    contract_version: str,
    calibration_method: str,
    threshold: float,
    config: dict[str, Any],
    partition_hash: str,
    data_manifest_hash: str,
    model_path: Path,
    root: Path,
) -> dict[str, Any]:
    """Write ``locks_dir/<lock_id>.json`` for a clean-tree selection; return the lock."""
    git = git_info(root)
    if git["dirty"]:
        raise SelectionError("selection lock requires a clean git tree")
    if not model_path.is_file():
        raise SelectionError(f"model artifact not found: {model_path}")
    payload: dict[str, Any] = {
        "model_run_id": model_run_id,
        "feature_set": feature_set,
        "feature_groups": list(feature_groups),
        "contract_version": contract_version,
        "calibration_method": calibration_method,
        "threshold": float(threshold),
        "config_hash": config_hash(config),
        "config": config,
        "partition_hash": partition_hash,
        "data_manifest_hash": data_manifest_hash,
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "git_commit": git["commit"],
        "library_versions": library_versions(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    lock_id = _lock_id(payload)
    lock = {"lock_id": lock_id, **payload}
    write_json_atomic(locks_dir / f"{lock_id}.json", lock)
    return lock


def load_lock(locks_dir: Path, lock_id: str) -> dict[str, Any]:
    path = locks_dir / f"{lock_id}.json"
    if not path.is_file():
        raise SelectionError(f"selection lock not found: {path}")
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelectionError(f"cannot read lock {path}: {exc}") from exc
    if not isinstance(lock, dict) or lock.get("lock_id") != lock_id:
        raise SelectionError(f"lock {path} is malformed")
    return lock


def verify_lock(lock: dict[str, Any], *, root: Path) -> None:
    """Re-check hashes and the clean tree; raise ``SelectionError`` on any mismatch."""
    git = git_info(root)
    if git["commit"] != lock.get("git_commit"):
        raise SelectionError("git commit differs from the lock; re-lock on the current commit")
    model_path = Path(str(lock["model_path"]))
    if not model_path.is_file():
        raise SelectionError(f"locked model artifact missing: {model_path}")
    if sha256_file(model_path) != lock.get("model_sha256"):
        raise SelectionError("locked model artifact hash mismatch")
    if config_hash(lock.get("config", {})) != lock.get("config_hash"):
        raise SelectionError("locked config hash mismatch")


__all__ = ["SelectionError", "create_lock", "load_lock", "verify_lock"]
