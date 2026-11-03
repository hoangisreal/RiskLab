"""Registry alias audit log (MLflow keeps no alias history)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def append_audit(
    path: Path,
    *,
    action: str,
    from_version: str | None,
    to_version: str | None,
    actor: str,
    reason: str = "",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "action": action,
        "from_version": from_version,
        "to_version": to_version,
        "actor": actor,
        "reason": reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


__all__ = ["append_audit", "read_audit"]
