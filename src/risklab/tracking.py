"""MLflow run setup shared by all experiments (MASTER_PLAN Section 9).

Clients never open a database or artifact path directly. They talk to the tracking server named by
``MLFLOW_TRACKING_URI`` (``make mlflow`` starts one on 127.0.0.1:5000). Every run carries the same
provenance tags: partition hash, config hashes, git commit, dirty flag and library versions.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import mlflow

from risklab.manifest import config_hash, git_info, library_versions, sha256_file

TRACKING_URI_ENV = "MLFLOW_TRACKING_URI"
DISABLE_HINT_ENV = "MLFLOW_DISABLE_AGENT_HINT"


class TrackingError(Exception):
    """Raised when the tracking server is not configured or unreachable."""


def require_tracking_uri() -> str:
    uri = os.environ.get(TRACKING_URI_ENV, "").strip()
    if not uri:
        raise TrackingError(
            f"{TRACKING_URI_ENV} is not set. Start the server with `make mlflow` and export "
            f"{TRACKING_URI_ENV}=http://127.0.0.1:5000"
        )
    if not uri.startswith(("http://", "https://")):
        raise TrackingError("MLFLOW_TRACKING_URI must point at an HTTP tracking server")
    return uri


def files_hash(paths: list[Path]) -> str:
    """SHA256 over the named files' hashes, in sorted path order."""
    digest = hashlib.sha256()
    for p in sorted(paths):
        digest.update(f"{p.name}:{sha256_file(p)}\n".encode())
    return digest.hexdigest()


def provenance_tags(
    *,
    root: Path,
    partition_hash: str,
    config: Mapping[str, Any],
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Tags every run must carry. ``config`` is the resolved experiment configuration."""
    git = git_info(root)
    tags = {
        "partition_hash": partition_hash,
        "config_hash": config_hash(config),
        "git_commit": str(git["commit"]),
        "git_dirty": str(git["dirty"]),
        **{f"lib.{k}": str(v) for k, v in library_versions().items()},
    }
    tags.update(extra or {})
    return tags


@contextmanager
def tracked_run(
    experiment: str, run_name: str, tags: Mapping[str, str], params: Mapping[str, Any]
) -> Iterator[str]:
    """Open an MLflow run on the configured server; yields the run id."""
    os.environ.setdefault(DISABLE_HINT_ENV, "1")
    uri = require_tracking_uri()
    try:
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(experiment)
        run = mlflow.start_run(run_name=run_name)
    except Exception as exc:
        raise TrackingError(f"Cannot reach the MLflow tracking server at {uri}: {exc}") from exc
    try:
        mlflow.set_tags(dict(tags))
        mlflow.log_params({k: _param(v) for k, v in params.items()})
        yield run.info.run_id
    except BaseException:
        mlflow.end_run(status="FAILED")
        raise
    else:
        mlflow.end_run()


def _param(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 500 else text[:497] + "..."


__all__ = [
    "TRACKING_URI_ENV",
    "TrackingError",
    "files_hash",
    "provenance_tags",
    "require_tracking_uri",
    "tracked_run",
]
