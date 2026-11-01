"""Run manifests shared by all pipeline steps (MASTER_PLAN Section 11.2)."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHUNK_SIZE = 1024 * 1024
JSON_FILE_MODE = 0o644
LIBRARIES = ("duckdb", "pyarrow", "numpy")


def sha256_file(path: Path | str, chunk_size: int = CHUNK_SIZE) -> str:
    """Hex SHA256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


def config_hash(config: Mapping[str, Any]) -> str:
    """Stable SHA256 of a resolved config dict (canonical JSON, sorted keys)."""
    return hashlib.sha256(_canonical_json(config).encode("utf-8")).hexdigest()


def git_info(cwd: Path | str | None = None) -> dict[str, Any]:
    """Return ``{"commit", "dirty"}``; both ``None`` outside a repo or without commits."""
    none: dict[str, Any] = {"commit": None, "dirty": None}

    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout

    commit = run("rev-parse", "--verify", "HEAD")
    if commit is None:
        return none
    status = run("status", "--porcelain")
    return {"commit": commit.strip(), "dirty": None if status is None else bool(status.strip())}


def library_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": platform.python_version()}
    for lib in LIBRARIES:
        try:
            versions[lib] = importlib.metadata.version(lib)
        except importlib.metadata.PackageNotFoundError:
            versions[lib] = None
    return versions


def hash_files(paths: Mapping[str, Path | str] | list[Path | str]) -> dict[str, str]:
    """SHA256 for each file. A list is keyed by path string; a mapping by its own keys."""
    items = paths.items() if isinstance(paths, Mapping) else ((str(p), p) for p in paths)
    return {str(k): sha256_file(p) for k, p in sorted(items, key=lambda kv: str(kv[0]))}


def build_manifest(
    step: str,
    *,
    config: Mapping[str, Any],
    inputs: Mapping[str, str] | None = None,
    outputs: Mapping[str, str] | None = None,
    summary: Mapping[str, Any] | None = None,
    git_cwd: Path | str | None = None,
) -> dict[str, Any]:
    """Build a manifest dict. ``inputs``/``outputs`` map names to SHA256 hashes."""
    return {
        "step": step,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config_hash": config_hash(config),
        "git": git_info(git_cwd),
        "versions": library_versions(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
        },
        "inputs": dict(sorted((inputs or {}).items())),
        "outputs": dict(sorted((outputs or {}).items())),
        "summary": dict(summary or {}),
    }


def write_json_atomic(path: Path | str, data: Mapping[str, Any]) -> Path:
    """Write JSON with sorted keys via a temp file in the same directory, then rename."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True, indent=2, default=str)
            fh.write("\n")
        os.chmod(tmp_name, JSON_FILE_MODE)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def write_manifest(
    path: Path | str,
    step: str,
    *,
    config: Mapping[str, Any],
    inputs: Mapping[str, str] | None = None,
    outputs: Mapping[str, str] | None = None,
    summary: Mapping[str, Any] | None = None,
    git_cwd: Path | str | None = None,
) -> dict[str, Any]:
    """Build and atomically write a step manifest; returns the manifest dict.

    ``git_cwd`` is the project root used to read the commit and dirty flag. It defaults to the
    current directory, so pipeline callers should pass the project root explicitly.
    """
    manifest = build_manifest(
        step,
        config=config,
        inputs=inputs,
        outputs=outputs,
        summary=summary,
        git_cwd=git_cwd if git_cwd is not None else Path.cwd(),
    )
    write_json_atomic(path, manifest)
    return manifest


__all__ = [
    "build_manifest",
    "config_hash",
    "git_info",
    "hash_files",
    "library_versions",
    "sha256_file",
    "write_json_atomic",
    "write_manifest",
]
