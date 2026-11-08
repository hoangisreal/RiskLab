"""Selection lock: freeze a choice before Test is opened."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from risklab.config import DataConfig, PartitionConfig
from risklab.manifest import config_hash, git_info, library_versions, sha256_file, write_json_atomic


class SelectionError(Exception):
    """Raised for invalid locks or failed lock verification."""


def protocol_hashes(root: Path) -> dict[str, str]:
    """Bind feature SQL and all experiment/cleaning configs to the selection."""
    hashes = {
        str(p.relative_to(root)): sha256_file(p)
        for directory, pattern in (("configs", "*.yaml"), ("sql", "*.sql"))
        for p in sorted((root / directory).rglob(pattern))
    }
    package = Path(__file__).resolve().parents[1]
    for relative in (
        "config.py",
        "data/clean.py",
        "data/loaders.py",
        "data/partition.py",
        "features/application.py",
        "features/historical.py",
        "features/build.py",
        "features/online.py",
        "features/history_clean.py",
        "features/payload.py",
        "experiments/e1.py",
        "experiments/e234.py",
        "models/pipelines.py",
        "models/calibrate.py",
        "models/train.py",
        "models/evaluate.py",
        "models/threshold.py",
        "monitoring/profile.py",
        "api/context.py",
        "api/service.py",
        "api/contract.py",
    ):
        hashes[f"src/risklab/{relative}"] = sha256_file(package / relative)
    return hashes


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
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise SelectionError("locked threshold must be finite and in [0, 1]")
    if calibration_method not in ("none", "sigmoid", "isotonic"):
        raise SelectionError("unknown calibration method")
    git = git_info(root)
    if git["dirty"] is not False or not git["commit"]:
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
        "protocol_hashes": protocol_hashes(root),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    lock_id = _lock_id(payload)
    lock = {"lock_id": lock_id, **payload}
    write_json_atomic(locks_dir / f"{lock_id}.json", lock)
    return lock


def load_lock(locks_dir: Path, lock_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{16}", lock_id):
        raise SelectionError("invalid lock id")
    path = locks_dir / f"{lock_id}.json"
    if not path.is_file():
        raise SelectionError(f"selection lock not found: {path}")
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelectionError(f"cannot read lock {path}: {exc}") from exc
    if not isinstance(lock, dict) or lock.get("lock_id") != lock_id:
        raise SelectionError(f"lock {path} is malformed")
    if _lock_id({k: v for k, v in lock.items() if k != "lock_id"}) != lock_id:
        raise SelectionError("lock content hash mismatch")
    return lock


def verify_lock(
    lock: dict[str, Any],
    *,
    root: Path,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
) -> None:
    """Re-check hashes and the clean tree; raise ``SelectionError`` on any mismatch."""
    git = git_info(root)
    if git["dirty"] is not False:
        raise SelectionError("lock verification requires a clean git tree")
    if git["commit"] != lock.get("git_commit"):
        raise SelectionError("git commit differs from the lock; re-lock on the current commit")
    model_path = Path(str(lock["model_path"]))
    if not model_path.is_file():
        raise SelectionError(f"locked model artifact missing: {model_path}")
    if sha256_file(model_path) != lock.get("model_sha256"):
        raise SelectionError("locked model artifact hash mismatch")
    if config_hash(lock.get("config", {})) != lock.get("config_hash"):
        raise SelectionError("locked config hash mismatch")
    if lock.get("library_versions") != library_versions():
        raise SelectionError("locked library versions differ from this runtime")
    if lock.get("protocol_hashes") != protocol_hashes(root):
        raise SelectionError("locked SQL/config files differ from the current protocol")
    if data_cfg is not None:
        from risklab.data.partition import PartitionError, load_verified_document

        if partition_cfg is None:
            raise SelectionError("partition config is required for data verification")
        try:
            document = load_verified_document(partition_cfg, data_cfg)
            if document["partition_hash"] != lock.get("partition_hash"):
                raise SelectionError("locked partition hash mismatch")
            if sha256_file(data_cfg.manifest_path) != lock.get("data_manifest_hash"):
                raise SelectionError("locked data manifest hash mismatch")
            manifest = json.loads(data_cfg.manifest_path.read_text(encoding="utf-8"))
            for name, expected in manifest["outputs"].items():
                if sha256_file(data_cfg.parquet_dir / name) != expected:
                    raise SelectionError(f"locked data changed: {name}")
            for name, expected in manifest["inputs"].items():
                if sha256_file(data_cfg.raw_dir / name) != expected:
                    raise SelectionError(f"locked raw data changed: {name}")
        except (OSError, ValueError, KeyError, PartitionError) as exc:
            raise SelectionError(f"cannot verify locked data: {exc}") from exc


__all__ = ["SelectionError", "create_lock", "load_lock", "verify_lock"]
