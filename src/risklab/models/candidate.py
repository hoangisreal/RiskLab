"""Package an already selected artifact; never refit a model after selection."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

from pydantic import Field

from risklab.config import _Strict, load_data_config, load_model
from risklab.data.loaders import partition_info
from risklab.features.historical import load_full_frame
from risklab.features.parity import verify_parity
from risklab.features.registry import load_registry
from risklab.manifest import config_hash, library_versions, sha256_file, write_json_atomic
from risklab.models.calibrate import predict_proba
from risklab.models.evaluate import compute_metrics
from risklab.models.select import protocol_hashes
from risklab.monitoring.profile import make_profile
from risklab.monitoring.service import MonitoringConfig


class ServingValidationConfig(_Strict):
    parity_sample_size: int = Field(ge=1)
    parity_seed: int
    parity_rtol: float = Field(gt=0)
    metric_atol: float = Field(ge=0)


def prepare_candidate(
    *, root: Path, config_dir: Path, model_path: Path | None = None
) -> dict[str, Any]:
    data_cfg = load_data_config(config_dir)
    reports = data_cfg.reports_dir
    e2 = json.loads((reports / "experiments/e2/summary.json").read_text())
    e3 = json.loads((reports / "experiments/e3/summary.json").read_text())
    e4 = json.loads((reports / "experiments/e4/summary.json").read_text())
    validation = load_model(config_dir, "serving_validation.yaml", ServingValidationConfig)
    groups = [g for g in e2["served_groups"] if g != "application"]
    for name, summary in (("E2", e2), ("E3", e3), ("E4", e4)):
        if summary.get("protocol_hashes") != protocol_hashes(root) or summary.get(
            "data_manifest_hash"
        ) != sha256_file(data_cfg.manifest_path):
            raise ValueError(f"{name} no longer matches the current source/config/data")
    method = e3["selection"]["chosen"]
    if not all(summary.get("run_id") for summary in (e2, e3, e4)):
        raise ValueError("tracked E2/E3/E4 decisions are required")
    if e4["calibration_method"] != method:
        raise ValueError("E4 calibration method differs from E3")
    if e3.get("protocol_hashes") and e3["protocol_hashes"] != protocol_hashes(root):
        raise ValueError("E3 SQL/config changed before candidate preparation")
    if e3.get("data_manifest_hash") and e3["data_manifest_hash"] != sha256_file(
        data_cfg.manifest_path
    ):
        raise ValueError("E3 data changed before candidate preparation")
    selected_path = model_path or Path(e3["model_path"])
    digest = sha256_file(selected_path)
    if e4.get("model_sha256") != digest:
        raise ValueError("E4 did not evaluate this frozen artifact")
    if e3.get("model_sha256") and digest != e3["model_sha256"]:
        raise ValueError("candidate differs from the saved E3 artifact")
    if e3.get("library_versions") and e3["library_versions"] != library_versions():
        raise ValueError("E3 library versions differ from this runtime")
    with open(selected_path, "rb") as fh:
        model = pickle.load(fh)
    X, y = load_full_frame("selection", "select", groups, config_dir=config_dir, root=root)
    columns = list(model.feature_names_in_)
    metrics = compute_metrics(y.to_numpy(), predict_proba(model, X[columns]))
    expected = e3["selection"]["metrics"][method]
    if any(abs(metrics[k] - expected[k]) > validation.metric_atol for k in metrics):
        raise ValueError("candidate does not reproduce the selected E3 metrics")
    parity = verify_parity(
        model,
        groups,
        config_dir=config_dir,
        root=root,
        sample_size=validation.parity_sample_size,
        seed=validation.parity_seed,
        rtol=validation.parity_rtol,
    )
    parity["model_sha256"] = digest
    if not parity["passed"]:
        raise ValueError(f"batch/online parity failed: {parity['failures']}")
    monitoring = load_model(config_dir, "monitoring.yaml", MonitoringConfig)
    train, _ = load_full_frame("train", "fit", groups, config_dir=config_dir, root=root)
    profile = make_profile(
        train[columns],
        predict_proba(model, X[columns]),
        model_sha256=digest,
        contract_version=load_registry(config_dir).contract_version,
        bins=monitoring.reference_bins,
        cdf_points=monitoring.reference_cdf_points,
        selection_labels=y.to_numpy(),
    )
    profile_path = (
        reports
        / "models"
        / "profiles"
        / config_hash(profile.model_dump())
        / "reference_profile.json"
    )
    write_json_atomic(profile_path, profile.model_dump())
    candidate = {
        "contract_version": load_registry(config_dir).contract_version,
        "protocol_hashes": protocol_hashes(root),
        "library_versions": library_versions(),
        "reference_profile_path": str(profile_path),
        "reference_profile_sha256": sha256_file(profile_path),
        "model_path": str(selected_path.resolve()),
        "model_sha256": digest,
        "feature_groups": groups,
        "feature_columns": columns,
        "calibration_method": method,
        "threshold": e4["threshold_choice"]["threshold"],
        "selection_metrics": metrics,
        "selection_ece": e3["selection"]["ece"][method],
        "selection_run_id": e3["run_id"],
        "parity": parity,
        "partition_hash": partition_info("train", config_dir=config_dir).partition_hash,
        "data_manifest_hash": sha256_file(data_cfg.manifest_path),
    }
    write_json_atomic(reports / "models/candidate.json", candidate)
    return candidate
