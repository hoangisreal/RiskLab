"""``risklab verify``: compare a fresh run against committed reference manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def verify_reference(reference_dir: Path, fresh_dir: Path) -> dict[str, Any]:
    """Compare manifest JSON files by hash fields; tolerances per MASTER_PLAN 11.3."""
    checks: list[dict[str, Any]] = []
    passed = True
    for ref_path in sorted(reference_dir.glob("*.json")):
        fresh_path = fresh_dir / ref_path.name
        if not fresh_path.is_file():
            checks.append({"file": ref_path.name, "status": "missing_fresh"})
            passed = False
            continue
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
        same_hash = (
            ref.get("partition_hash") == fresh.get("partition_hash")
            if "partition_hash" in ref
            else None
        )
        checks.append(
            {"file": ref_path.name, "status": "compared", "partition_hash_match": same_hash}
        )
        if same_hash is False:
            passed = False
    return {
        "passed": passed,
        "checks": checks,
        "reference_dir": str(reference_dir),
        "fresh_dir": str(fresh_dir),
    }


__all__ = ["verify_reference"]
