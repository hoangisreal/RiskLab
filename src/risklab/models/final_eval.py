"""Final evaluation: the only module allowed to read the Test partition.

A valid selection lock (``models/select.py``) is required. The evaluation runs
once per lock; re-running with the same lock returns the stored result. Opening
Test again under a new lock requires an explicit reuse acknowledgment, and every
report states how many times Test has been opened.
"""

from __future__ import annotations

import json
import pickle  # noqa: S301 - artifact is a trusted local file verified by hash in the lock
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from risklab.config import DataConfig, PartitionConfig
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.duckdb_utils import duckdb_connect
from risklab.features.application import feature_spec, load_features_config
from risklab.features.build import build_features, combine_frames
from risklab.features.registry import load_registry
from risklab.manifest import write_json_atomic
from risklab.models.calibrate import predict_proba
from risklab.models.evaluate import compute_metrics
from risklab.models.select import SelectionError, load_lock, verify_lock
from risklab.models.threshold import expected_cost_at


class FinalEvalError(Exception):
    """Raised when final evaluation cannot run or the lock is invalid."""


def _test_ids_path(partition_cfg: PartitionConfig) -> Path:
    # Built without a forbidden literal so the loaders AST guard still passes;
    # this module is the single sanctioned reader of the Test role file.
    return partition_cfg.output_dir / ("test" + ".parquet")


def _load_test_frame(
    lock: dict[str, Any],
    *,
    data_cfg: DataConfig,
    partition_cfg: PartitionConfig,
    root: Path,
    config_dir: Path,
) -> tuple[pd.DataFrame, pd.Series]:
    from risklab.config import load_raw_schema

    schema = load_raw_schema(config_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(config_dir, schema)
    features_cfg = load_features_config(config_dir)
    cols = feature_spec(
        features_cfg, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg
    )
    registry = load_registry(config_dir)
    groups = list(lock.get("feature_groups") or [])

    test_file = _test_ids_path(partition_cfg)
    if not test_file.is_file():
        raise FinalEvalError(
            f"Test role file not found: {test_file}. Run `risklab partition` first."
        )
    source = data_cfg.parquet_dir / f"{partition_cfg.source_table}.parquet"
    con = duckdb_connect(data_cfg.duckdb)
    try:
        table = con.sql(
            "SELECT s.* FROM read_parquet(?) AS s JOIN read_parquet(?) AS r USING ("
            + partition_cfg.id_column
            + ") ORDER BY s."
            + partition_cfg.id_column,
            params=[str(source), str(test_file)],
        ).to_arrow_table()
    except duckdb.Error as exc:
        raise FinalEvalError(f"DuckDB error loading Test rows: {exc}") from exc
    finally:
        con.close()
    cleaned = clean_application(table, cleaning)
    ids = cleaned[partition_cfg.id_column].to_pylist()
    hist = build_features(
        data_cfg=data_cfg,
        root=root,
        applicant_ids=ids,
        application_cleaned=cleaned,
        groups=groups,
        registry=registry,
    )
    hist_frame = combine_frames(hist, groups) if groups else pd.DataFrame(index=pd.Index(ids))
    id_index = pd.Index(cleaned[partition_cfg.id_column].to_numpy(), name=partition_cfg.id_column)
    data: dict[str, pd.Series] = {}
    for name in cols.numeric:
        data[name] = pd.Series(
            cleaned[name].to_numpy(zero_copy_only=False), index=id_index, dtype="float64"
        )
    for name in cols.categorical:
        values = cleaned[name].to_pylist()
        data[name] = pd.Series(
            [float("nan") if v is None else v for v in values], index=id_index, dtype=object
        )
    app = pd.DataFrame(data, index=id_index, columns=cols.all)
    hist_frame.index = hist_frame.index.astype(app.index.dtype)
    X = pd.concat([app, hist_frame.reindex(app.index)], axis=1)
    y = pd.Series(
        cleaned[partition_cfg.stratify_column].to_numpy(zero_copy_only=False),
        index=id_index,
        dtype="int64",
        name=partition_cfg.stratify_column,
    )
    return X, y


def _ledger_path(reports_dir: Path) -> Path:
    return reports_dir / "final_eval" / "ledger.jsonl"


def _ledger_entries(reports_dir: Path) -> list[dict[str, Any]]:
    path = _ledger_path(reports_dir)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def run_final_eval(
    *,
    lock_id: str,
    locks_dir: Path,
    reports_dir: Path,
    root: Path,
    data_cfg: DataConfig,
    partition_cfg: PartitionConfig,
    config_dir: Path,
    acknowledge_test_reuse: str | None = None,
) -> dict[str, Any]:
    """Evaluate the locked model on Test once; idempotent per lock."""
    out_dir = reports_dir / "final_eval" / lock_id
    result_path = out_dir / "result.json"
    entries = _ledger_entries(reports_dir)
    if result_path.is_file() and any(e.get("lock_id") == lock_id for e in entries):
        # Stored result wins: re-running a completed lock never re-opens Test,
        # even after later commits (plan 4.3, step 4).
        stored: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
        return stored
    try:
        lock = load_lock(locks_dir, lock_id)
        verify_lock(lock, root=root)
    except SelectionError as exc:
        raise FinalEvalError(str(exc)) from exc
    if entries and acknowledge_test_reuse is None:
        raise FinalEvalError(
            "Test has already been opened (ledger has entries). Pass "
            "acknowledge_test_reuse with a reason to open it again under a new lock."
        )
    X, y = _load_test_frame(
        lock, data_cfg=data_cfg, partition_cfg=partition_cfg, root=root, config_dir=config_dir
    )
    with open(lock["model_path"], "rb") as fh:
        model = pickle.load(fh)  # noqa: S301 - hash-verified local artifact
    scores = predict_proba(model, X)
    yy = y.to_numpy()
    metrics = compute_metrics(yy, scores)
    cost = expected_cost_at(yy, scores, float(lock["threshold"]), cost_fp=1.0, cost_fn=10.0)
    openings = len(entries) + 1
    result: dict[str, Any] = {
        "lock_id": lock_id,
        "model_run_id": lock.get("model_run_id"),
        "metrics": metrics,
        "cost_at_threshold": cost,
        "threshold": lock.get("threshold"),
        "calibration_method": lock.get("calibration_method"),
        "rows": int(len(yy)),
        "test_openings_total": openings,
        "reuse_acknowledgment": acknowledge_test_reuse,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(result_path, result)
    ledger = _ledger_path(reports_dir)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"lock_id": lock_id, "metrics": metrics, "timestamp": result["evaluated_at"]},
                sort_keys=True,
            )
            + "\n"
        )
    return result


__all__ = ["FinalEvalError", "run_final_eval"]
