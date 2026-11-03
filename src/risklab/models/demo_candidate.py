"""A deliberately corrupted-label artifact for the declared rollback exercise only."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Literal

import mlflow
import numpy as np
from sklearn.base import clone

from risklab.config import _Strict, load_data_config, load_model
from risklab.data.loaders import partition_info
from risklab.features.historical import load_full_frame
from risklab.features.parity import verify_parity
from risklab.manifest import config_hash, library_versions, sha256_file, write_json_atomic
from risklab.models.calibrate import (
    expected_calibration_error,
    load_calibration_config,
    predict_proba,
)
from risklab.models.candidate import ServingValidationConfig
from risklab.models.evaluate import compute_metrics, save_predictions
from risklab.models.explain import underlying_pipeline
from risklab.models.select import protocol_hashes
from risklab.monitoring.policy import load_candidate
from risklab.monitoring.profile import make_profile
from risklab.monitoring.service import MonitoringConfig
from risklab.tracking import provenance_tags, tracked_run


class RollbackConfig(_Strict):
    experiment_name: str
    seed: int
    corruption: Literal["permute_train_labels"]
    calibration_method: Literal["none"]
    threshold_policy: Literal["copy_verified_champion"]
    monitoring_scenario: Literal["control"]
    data_drift_scenario: Literal["label_flip"]
    batch_seed: int
    force_reason: str


def prepare_bad_candidate(candidate_path: Path, *, config_dir: Path, root: Path) -> Path:
    """Fit a separate diagnostic artifact on corrupted Train labels; never refit the champion."""
    cfg = load_model(config_dir, "experiments/rollback.yaml", RollbackConfig)
    data = load_data_config(config_dir)
    source = json.loads(candidate_path.read_text())
    if (
        source["protocol_hashes"] != protocol_hashes(root)
        or source["library_versions"] != library_versions()
    ):
        raise ValueError("the verified champion candidate differs from this protocol/runtime")
    loaded, _ = load_candidate(candidate_path)
    groups = loaded.feature_groups
    train, y_train = load_full_frame("train", "fit", groups, config_dir=config_dir, root=root)
    selection, y_selection = load_full_frame(
        "selection", "select", groups, config_dir=config_dir, root=root
    )
    columns = list(loaded.model.feature_names_in_)
    bad = clone(underlying_pipeline(loaded.model))
    corrupted_labels = np.random.default_rng(cfg.seed).permutation(y_train.to_numpy())
    bad.fit(train[columns], corrupted_labels)
    scores = predict_proba(bad, selection[columns])
    metrics = compute_metrics(y_selection.to_numpy(), scores)
    payload = pickle.dumps(bad)
    digest = hashlib.sha256(payload).hexdigest()
    output = data.reports_dir / "rollback-demo" / digest
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / "diagnostic.pkl"
    if not model_path.is_file():
        model_path.write_bytes(payload)
    predictions = save_predictions(
        output / "selection_predictions.parquet",
        selection.index,
        y_selection.to_numpy(),
        {"diagnostic": scores},
    )
    validation = load_model(config_dir, "serving_validation.yaml", ServingValidationConfig)
    parity = verify_parity(
        bad,
        groups,
        config_dir=config_dir,
        root=root,
        sample_size=validation.parity_sample_size,
        seed=validation.parity_seed,
        rtol=validation.parity_rtol,
    )
    parity["model_sha256"] = digest
    if not parity["passed"]:
        raise ValueError("diagnostic artifact failed serving parity")
    monitoring = load_model(config_dir, "monitoring.yaml", MonitoringConfig)
    profile = make_profile(
        train[columns],
        scores,
        model_sha256=digest,
        contract_version=loaded.contract_version,
        bins=monitoring.reference_bins,
        cdf_points=monitoring.reference_cdf_points,
        selection_labels=y_selection.to_numpy(),
    )
    profile_path = output / "reference_profile.json"
    write_json_atomic(profile_path, profile.model_dump())
    partition_hash = partition_info("train", config_dir=config_dir).partition_hash
    calibration = load_calibration_config(config_dir)
    tags = provenance_tags(
        root=root,
        partition_hash=partition_hash,
        config=cfg.model_dump(),
        extra={
            "purpose": "rollback_demo_only",
            "fit_role": "train",
            "eval_role": "selection",
            "model_sha256": digest,
            "source_run_id": source["selection_run_id"],
        },
    )
    with tracked_run(
        cfg.experiment_name, "corrupted-train-labels", tags, cfg.model_dump()
    ) as run_id:
        mlflow.log_metrics({f"selection_{name}": value for name, value in metrics.items()})
        mlflow.log_artifact(str(predictions))
        mlflow.log_artifact(str(model_path))
        candidate: dict[str, Any] = {
            "purpose": "rollback_demo_only",
            "contract_version": loaded.contract_version,
            "protocol_hashes": protocol_hashes(root),
            "library_versions": library_versions(),
            "reference_profile_path": str(profile_path),
            "reference_profile_sha256": sha256_file(profile_path),
            "model_path": str(model_path),
            "model_sha256": digest,
            "feature_groups": groups,
            "feature_columns": columns,
            "calibration_method": cfg.calibration_method,
            "threshold": loaded.threshold,
            "selection_metrics": metrics,
            "selection_ece": expected_calibration_error(
                y_selection.to_numpy(), scores, n_bins=calibration.ece_bins
            ),
            "selection_run_id": run_id,
            "parity": parity,
            "partition_hash": partition_hash,
            "data_manifest_hash": sha256_file(data.manifest_path),
            "experiment_config_hash": config_hash(cfg.model_dump()),
        }
        mlflow.log_dict(candidate, "candidate.json")
    return write_json_atomic(output / "candidate.json", candidate)
