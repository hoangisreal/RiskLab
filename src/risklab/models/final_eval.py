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
from pydantic import Field

from risklab.config import DataConfig, PartitionConfig, _Strict, load_model
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.duckdb_utils import duckdb_connect
from risklab.features.application import feature_spec, frame_from_table, load_features_config
from risklab.features.build import build_features, combine_frames
from risklab.features.registry import load_registry
from risklab.manifest import sha256_file, write_json_atomic, write_manifest
from risklab.models.calibrate import (
    expected_calibration_error,
    load_calibration_config,
    predict_proba,
)
from risklab.models.evaluate import bootstrap_metrics, compute_metrics, save_predictions
from risklab.models.select import SelectionError, load_lock, verify_lock
from risklab.models.threshold import expected_cost_at


class FinalEvaluationConfig(_Strict):
    bootstrap_resamples: int = Field(default=1000, ge=100)
    bootstrap_seed: int = 20261101
    ci_level: float = Field(default=0.95, gt=0, lt=1)
    min_n: int = Field(default=200, ge=1)
    min_positives: int = Field(default=20, ge=1)


class FinalEvalError(Exception):
    """Raised when final evaluation cannot run or the lock is invalid."""


def _test_ids_path(partition_cfg: PartitionConfig) -> Path:
    return partition_cfg.output_dir / "test.parquet"


def _load_test_frame(
    lock: dict[str, Any],
    *,
    data_cfg: DataConfig,
    partition_cfg: PartitionConfig,
    root: Path,
    config_dir: Path,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
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
    engineered = list(dict.fromkeys(["application", *groups, "bureau"]))
    hist = build_features(
        data_cfg=data_cfg,
        root=root,
        applicant_ids=ids,
        application_cleaned=cleaned,
        groups=engineered,
        registry=registry,
    )
    hist_frame = combine_frames(hist, engineered)
    app = frame_from_table(cleaned, cols, id_column=partition_cfg.id_column)
    id_index = app.index
    hist_frame.index = hist_frame.index.astype(app.index.dtype)
    X = pd.concat([app, hist_frame.reindex(app.index)], axis=1)
    y = pd.Series(
        cleaned[partition_cfg.stratify_column].to_numpy(zero_copy_only=False),
        index=id_index,
        dtype="int64",
        name=partition_cfg.stratify_column,
    )
    metadata = pd.DataFrame(
        {
            c: cleaned[c].to_pylist()
            for c in (
                "CODE_GENDER",
                "DAYS_BIRTH",
                "NAME_INCOME_TYPE",
                "NAME_EDUCATION_TYPE",
                "REGION_RATING_CLIENT",
                "NAME_CONTRACT_TYPE",
            )
            if c in cleaned.column_names
        },
        index=id_index,
    )
    if "bureau_record_count" in hist_frame:
        metadata["bureau_record_count"] = hist_frame["bureau_record_count"]
    return X, y, metadata


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


def _run_final_eval(
    *,
    lock_id: str,
    locks_dir: Path,
    reports_dir: Path,
    root: Path,
    data_cfg: DataConfig,
    partition_cfg: PartitionConfig,
    config_dir: Path,
    acknowledge_test_reuse: str | None = None,
    tracking: bool = False,
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
        verify_lock(lock, root=root, data_cfg=data_cfg, partition_cfg=partition_cfg)
    except SelectionError as exc:
        raise FinalEvalError(str(exc)) from exc
    if entries and not (acknowledge_test_reuse or "").strip():
        raise FinalEvalError(
            "Test has already been opened (ledger has entries). Pass "
            "acknowledge_test_reuse with a reason to open it again under a new lock."
        )
    from contextlib import nullcontext

    from risklab.models.threshold import CostConfig, load_cost_config
    from risklab.monitoring.performance import subgroup_report
    from risklab.monitoring.service import MonitoringConfig, subgroup_labels
    from risklab.tracking import tracked_run

    evaluation = FinalEvaluationConfig.model_validate(lock.get("config", {}).get("evaluation", {}))
    cost_cfg = CostConfig.model_validate(
        lock.get("config", {}).get("cost", load_cost_config(config_dir).model_dump())
    )
    with open(lock["model_path"], "rb") as fh:
        model = pickle.load(fh)
    monitoring_cfg = load_model(config_dir, "monitoring.yaml", MonitoringConfig)
    calibration_cfg = load_calibration_config(config_dir)
    openings = len(entries) + 1
    context = (
        tracked_run(
            "risklab-final-eval",
            lock_id,
            {"lock_id": lock_id, "model_run_id": str(lock["model_run_id"])},
            evaluation.model_dump(),
        )
        if tracking
        else nullcontext(None)
    )
    with context as evaluation_run_id:
        # Reserve the opening before any Test read. A crash cannot silently erase an opening.
        ledger = _ledger_path(reports_dir)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "lock_id": lock_id,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "run_id": evaluation_run_id,
                        "reuse_acknowledgment": acknowledge_test_reuse,
                        "test_openings_total": openings,
                    }
                )
                + "\n"
            )
            fh.flush()
            import os

            os.fsync(fh.fileno())
        X, y, metadata = _load_test_frame(
            lock, data_cfg=data_cfg, partition_cfg=partition_cfg, root=root, config_dir=config_dir
        )
        X = X[list(model.feature_names_in_)]
        scores = predict_proba(model, X)
        yy = y.to_numpy()
        metrics = compute_metrics(yy, scores)
        if cost_cfg.policy == "per_loan":
            assert cost_cfg.loan is not None
            cost = expected_cost_at(
                yy,
                scores,
                float(lock["threshold"]),
                cost_fp=cost_cfg.loan.margin,
                cost_fn=cost_cfg.loan.lgd,
                exposure=X["AMT_CREDIT"].to_numpy(dtype=float),
            )
        else:
            cost = expected_cost_at(
                yy,
                scores,
                float(lock["threshold"]),
                cost_fp=cost_cfg.cost_fp,
                cost_fn=cost_cfg.cost_fn,
            )
        result = {
            "lock_id": lock_id,
            "model_run_id": lock["model_run_id"],
            "run_id": evaluation_run_id,
            "metrics": metrics,
            "calibration": {
                "ece": expected_calibration_error(yy, scores, n_bins=calibration_cfg.ece_bins),
                "calibration_in_the_large": float(scores.mean() - yy.mean()),
            },
            "bootstrap_method": "stratified_percentile",
            "ci_level": evaluation.ci_level,
            "metric_ci": bootstrap_metrics(
                yy,
                scores,
                n_resamples=evaluation.bootstrap_resamples,
                seed=evaluation.bootstrap_seed,
                ci_level=evaluation.ci_level,
            ),
            "subgroups": subgroup_report(
                metadata,
                yy,
                scores,
                subgroup_labels(metadata, age_edges=monitoring_cfg.age_band_edges),
                threshold=float(lock["threshold"]),
                min_n=evaluation.min_n,
                min_positives=evaluation.min_positives,
                seed=evaluation.bootstrap_seed,
                n_resamples=evaluation.bootstrap_resamples,
                ci_level=evaluation.ci_level,
            ),
            "cost_at_threshold": cost,
            "threshold": lock["threshold"],
            "calibration_method": lock["calibration_method"],
            "rows": int(len(yy)),
            "test_openings_total": openings,
            "reuse_acknowledgment": acknowledge_test_reuse,
            "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        predictions = save_predictions(
            out_dir / "predictions.parquet", X.index, yy, {"model": scores}
        )
        manifest_path = reports_dir / "manifests/final-eval.json"
        write_manifest(
            manifest_path,
            "final-eval",
            config=lock["config"],
            inputs={
                "data_manifest": lock["data_manifest_hash"],
                "model": lock["model_sha256"],
                "partition": lock["partition_hash"],
            },
            outputs={"predictions": sha256_file(predictions)},
            summary={
                "lock_id": lock_id,
                "run_id": evaluation_run_id,
                "rows": len(yy),
                "test_openings_total": openings,
            },
            git_cwd=root,
        )
        if tracking:
            import mlflow

            mlflow.log_metrics(metrics)
            mlflow.log_dict(result, "result.json")
            mlflow.log_artifact(str(predictions))
            mlflow.log_artifact(str(manifest_path), "manifests")
        write_json_atomic(result_path, result)
    return result


__all__ = ["FinalEvalError", "run_final_eval"]


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
    tracking: bool = False,
) -> dict[str, Any]:
    """Serialize openings and retain a durable attempt record even if evaluation crashes."""
    import fcntl

    ledger = _ledger_path(reports_dir)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger.with_suffix(".lock"), "a", encoding="utf-8") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        return _run_final_eval(
            lock_id=lock_id,
            locks_dir=locks_dir,
            reports_dir=reports_dir,
            root=root,
            data_cfg=data_cfg,
            partition_cfg=partition_cfg,
            config_dir=config_dir,
            acknowledge_test_reuse=acknowledge_test_reuse,
            tracking=tracking,
        )
