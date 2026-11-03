"""Report cached aggregate evaluation evidence for the exact pinned serving bundle."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from risklab.models.select import SelectionError, load_lock
from risklab.registry.bundle import ServingConfig


def final_evaluation_summary(
    reports_dir: Path, serving: ServingConfig | None
) -> dict[str, Any] | None:
    """Read completed aggregate results only; never access Test rows or predictions."""
    if serving is None:
        return None
    try:
        lock = load_lock(reports_dir / "locks", serving.lock_id)
        if (
            lock["model_sha256"] != serving.model_sha256
            or lock["model_run_id"] != serving.selection_run_id
            or lock["threshold"] != serving.threshold
            or lock["calibration_method"] != serving.calibration_method
        ):
            return None
        directory = reports_dir / "final_eval"
        result = json.loads((directory / serving.lock_id / "result.json").read_text())
        entries = [
            json.loads(line)
            for line in (directory / "ledger.jsonl").read_text().splitlines()
            if line.strip()
        ]
        if (
            result["lock_id"] != serving.lock_id
            or result["model_run_id"] != serving.selection_run_id
            or not isinstance(result["run_id"], str)
            or not result["run_id"].strip()
            or result["threshold"] != serving.threshold
            or result["calibration_method"] != serving.calibration_method
            or not any(
                entry.get("lock_id") == serving.lock_id
                and entry.get("run_id") == result["run_id"]
                and entry.get("test_openings_total") == result["test_openings_total"]
                for entry in entries
            )
        ):
            return None
        ServingConfig.valid_metrics(result["metrics"])
        if set(result["metric_ci"]) != set(result["metrics"]):
            return None
        for interval in result["metric_ci"].values():
            low, high = interval["ci_low"], interval["ci_high"]
            if not (math.isfinite(low) and math.isfinite(high) and 0 <= low <= high):
                return None
        if not (0 < result["ci_level"] < 1 and result["rows"] > 0):
            return None
        return {
            key: result[key]
            for key in (
                "lock_id",
                "model_run_id",
                "run_id",
                "metrics",
                "metric_ci",
                "ci_level",
                "rows",
                "test_openings_total",
                "evaluated_at",
            )
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SelectionError):
        return None
