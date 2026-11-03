"""Compare stable provenance and aggregate metrics against explicit references."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pydantic import Field

from risklab.config import _Strict
from risklab.manifest import write_json_atomic


class VerificationConfig(_Strict):
    metric_atol: float = Field(default=1e-6, ge=0)
    feature_rtol: float = Field(default=1e-9, ge=0)


def _differences(expected: Any, actual: Any, path: str, atol: float) -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [path]
        return [
            diff
            for key, value in expected.items()
            for diff in (
                _differences(value, actual[key], f"{path}.{key}", atol)
                if key in actual
                else [f"{path}.{key}: missing"]
            )
        ]
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [path]
        return [
            d
            for i, v in enumerate(expected)
            for d in _differences(v, actual[i], f"{path}[{i}]", atol)
        ]
    if isinstance(expected, float):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return [path]
        return [] if math.isfinite(actual) and abs(expected - actual) <= atol else [path]
    return [] if type(expected) is type(actual) and expected == actual else [path]


def _has_values(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_values(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_values(v) for v in value)
    return value is not None and value != ""


def verify_reference(
    reference_dir: Path,
    fresh_dir: Path,
    *,
    metric_atol: float = 1e-6,
) -> dict[str, Any]:
    """Compare reference subsets; missing, malformed, or empty references fail closed.

    A spec has ``path`` relative to the fresh reports root and ``expected`` stable
    fields. Legacy manifest JSONs are compared directly in ``fresh_dir``.
    """
    checks: list[dict[str, Any]] = []
    references = sorted(reference_dir.glob("*.json"))
    if not references:
        checks.append({"status": "missing_or_empty_reference"})
    for ref_path in references:
        try:
            ref = json.loads(ref_path.read_text(encoding="utf-8"))
            relative = Path(ref.get("path", ref_path.name))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("reference path must stay inside the fresh reports directory")
            expected = ref.get("expected", ref)
            if not isinstance(expected, dict) or not expected:
                raise ValueError("reference must contain non-empty expected fields")
            if not any(
                k in expected and _has_values(expected[k])
                for k in (
                    "inputs",
                    "outputs",
                    "partition_hash",
                    "selection_metrics",
                    "selection",
                    "ablation",
                    "threshold_choice",
                )
            ):
                raise ValueError("reference contains no provenance or metrics")
            actual = json.loads((fresh_dir / relative).read_text(encoding="utf-8"))
            # Hashes/integers are exact. Only explicitly identified metrics use tolerance.
            atol = metric_atol if ref.get("kind") == "metrics" else 0.0
            differences = _differences(expected, actual, "root", atol)
            checks.append(
                {
                    "file": ref_path.name,
                    "status": "mismatch" if differences else "ok",
                    "differences": differences,
                }
            )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            checks.append({"file": ref_path.name, "status": "invalid", "error": str(exc)})
    return {
        "passed": bool(checks) and all(c["status"] == "ok" for c in checks),
        "checks": checks,
        "reference_dir": str(reference_dir),
        "fresh_dir": str(fresh_dir),
    }


def snapshot_reference(reports_dir: Path, reference_dir: Path) -> list[Path]:
    """Export only stable hashes, counts and Selection aggregates; never row-level data."""
    written = []
    for name in ("ingest", "partition", "validate"):
        source = reports_dir / "manifests" / f"{name}.json"
        if not source.is_file():
            continue
        doc = json.loads(source.read_text(encoding="utf-8"))
        expected = {k: doc[k] for k in ("step", "inputs") if k in doc}
        if name == "validate":
            expected["inputs"] = {k: v for k, v in doc["inputs"].items() if k.endswith(".csv")}
            expected["summary"] = doc["summary"]
        if name == "partition":
            expected["outputs"] = doc["outputs"]
        if name == "ingest":
            expected["summary"] = {
                "tables": {
                    t: {
                        k: v for k, v in row.items() if k in ("columns", "csv_rows", "parquet_rows")
                    }
                    for t, row in doc["summary"]["tables"].items()
                }
            }
        written.append(
            write_json_atomic(
                reference_dir / f"{name}.json",
                {
                    "path": f"manifests/{name}.json",
                    "kind": "provenance",
                    "expected": expected,
                },
            )
        )
    fields = {
        "e1": ["selection_metrics"],
        "e1_served": ["selection_metrics", "decision"],
        "e2": ["ablation", "served_groups", "leave_one_group_out"],
        "e3": ["selection"],
        "e4": ["threshold_choice", "sensitivity", "loan_sensitivity", "cost_config"],
        "policy": ["selection_metrics", "comparisons_vs_default", "policy"],
    }
    for experiment, keys in fields.items():
        source = reports_dir / "experiments" / experiment / "summary.json"
        if not source.is_file():
            continue
        doc = json.loads(source.read_text(encoding="utf-8"))
        source_runs = doc.get("run_ids") or doc.get("run_id")
        if not source_runs:
            raise ValueError(f"{experiment} has no tracked run; cannot publish its metrics")
        written.append(
            write_json_atomic(
                reference_dir / f"{experiment}.json",
                {
                    "path": f"experiments/{experiment}/summary.json",
                    "kind": "metrics",
                    "source_run_ids": source_runs,
                    "expected": {
                        k: doc[k] for k in [*keys, "partition_hash", "contract_version"] if k in doc
                    },
                },
            )
        )
    if not written:
        raise ValueError("no manifests or experiment summaries available for a reference")
    return written


def compare_feature_tables(reference_dir: Path, fresh_dir: Path, *, rtol: float) -> bool:
    """Compare local feature baselines by applicant key, including missing-value masks."""
    import numpy as np
    import pandas as pd

    paths = sorted(reference_dir.glob("*.parquet"))
    if not paths:
        return False
    for ref_path in paths:
        fresh = fresh_dir / ref_path.name
        if not fresh.is_file():
            return False
        left = pd.read_parquet(ref_path).set_index("SK_ID_CURR").sort_index()
        right = pd.read_parquet(fresh).set_index("SK_ID_CURR").sort_index()
        if (
            not left.index.is_unique
            or not right.index.is_unique
            or not left.index.equals(right.index)
            or set(left.columns) != set(right.columns)
        ):
            return False
        if not np.allclose(
            left.to_numpy(), right[left.columns].to_numpy(), rtol=rtol, atol=0, equal_nan=True
        ):
            return False
    return True
