"""Registry alias audit log (MLflow keeps no alias history)."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def append_audit(
    path: Path,
    *,
    action: str,
    from_version: str | None,
    to_version: str | None,
    actor: str,
    reason: str = "",
    operation_id: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "action": action,
        "from_version": from_version,
        "to_version": to_version,
        "actor": actor,
        "reason": reason,
    }
    if operation_id is not None:
        entry["operation_id"] = operation_id
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return entry


def read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


__all__ = ["append_audit", "read_audit"]


def change_alias(
    path: Path,
    *,
    action: str,
    from_version: str | None,
    to_version: str,
    reason: str = "",
) -> None:
    """Journal intent before the remote mutation; serialize local CLI operators.

    MLflow has no atomic alias+audit transaction. An incomplete intent blocks
    replay until its outcome is reconciled against the actual alias.
    """
    from risklab.registry.client import RegistryError, champion_info_or_none, set_alias

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        history = read_audit(path)
        completed = {e.get("operation_id") for e in history if not e["action"].endswith(".intent")}
        for intent in history:
            if not intent["action"].endswith(".intent") or intent.get("operation_id") in completed:
                continue
            current = champion_info_or_none()
            actual = current["version"] if current else None
            if actual == intent["to_version"]:
                outcome = intent["action"].removesuffix(".intent")
            elif actual == intent["from_version"]:
                outcome = "alias.not_applied"
            else:
                raise RegistryError(
                    "Unresolved alias operation; inspect registry and audit before retry"
                )
            append_audit(
                path,
                action=outcome,
                from_version=intent["from_version"],
                to_version=intent["to_version"],
                actor="cli-reconcile",
                reason=intent["reason"],
                operation_id=intent["operation_id"],
            )
        current = champion_info_or_none()
        actual = current["version"] if current else None
        if actual != from_version:
            raise RegistryError("champion changed before alias mutation; re-evaluate the gate")
        operation_id = str(uuid4())
        kwargs: dict[str, Any] = dict(
            from_version=from_version,
            to_version=to_version,
            actor="cli",
            reason=reason,
            operation_id=operation_id,
        )
        append_audit(path, action=action + ".intent", **kwargs)
        set_alias(to_version)
        append_audit(path, action=action, **kwargs)
