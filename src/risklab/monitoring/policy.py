"""Predeclared control calibration with a separate held-out false-alarm measurement."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import Field

from risklab.config import _Strict
from risklab.manifest import config_hash, write_json_atomic
from risklab.models.calibrate import predict_proba
from risklab.monitoring.profile import ReferenceProfile
from risklab.monitoring.service import (
    MonitoringConfig,
    apply_alert_thresholds,
    evaluate_prepared_batch,
    load_monitoring_pool,
)


class AlertPolicy(_Strict):
    model_sha256: str
    profile_hash: str
    config_hash: str
    scale: float = Field(ge=1)
    run_id: str
    calibration_batches: int = Field(ge=1)
    validation_batches: int = Field(ge=1)
    calibration_false_alarm_rate: float = Field(ge=0, le=1)
    validation_false_alarm_rate: float = Field(ge=0, le=1)
    passed: bool


def scaled_config(config: MonitoringConfig, scale: float) -> MonitoringConfig:
    updates = {
        key: getattr(config, key) * scale
        for key in (
            "psi_alert",
            "performance_auc_drop",
            "performance_log_loss_increase",
            "performance_brier_increase",
        )
    }
    updates.update(
        {key: min(1.0, getattr(config, key) * scale) for key in ("ks_alert", "missing_rate_alert")}
    )
    return MonitoringConfig.model_validate(config.model_dump() | updates)


def effective_config(
    config: MonitoringConfig, profile: ReferenceProfile, *, reports_dir: Path
) -> tuple[MonitoringConfig, str | None]:
    path = reports_dir / "monitoring/alert-policy" / f"{profile.model_sha256}.json"
    if not path.is_file():
        return config, None
    policy = AlertPolicy.model_validate_json(path.read_text())
    if (
        policy.model_sha256 != profile.model_sha256
        or policy.profile_hash != config_hash(profile.model_dump())
        or policy.config_hash != config_hash(config.model_dump())
    ):
        raise ValueError("monitoring alert policy is bound to a different model/profile/config")
    if not policy.passed:
        raise ValueError("monitoring policy failed its held-out control target")
    return scaled_config(config, policy.scale), policy.run_id


def calibrate_controls(
    loaded: Any,
    profile: ReferenceProfile,
    *,
    config: MonitoringConfig,
    config_dir: Path,
    root: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    """Only Monitoring rows are sampled. No model, transform, score bin or threshold is fitted."""
    import mlflow

    from risklab.monitoring.service import registered_batches
    from risklab.tracking import tracked_run

    pool, labels = load_monitoring_pool(
        list(dict.fromkeys([*loaded.feature_groups, "bureau"])), config_dir=config_dir, root=root
    )
    columns = list(loaded.model.feature_names_in_)
    all_scores = predict_proba(loaded.model, pool[columns])
    existing = registered_batches(root)

    def controls(name: str, count: int, first_seed: int) -> list[dict[str, Any]]:
        reports = []
        for i in range(count):
            seed = first_seed + i
            batch_id = f"control-{name}-{seed}"
            existing[batch_id] = dict(
                batch_id=batch_id, scenario="control", seed=seed, rows=config.batch_size
            )
            idx = np.random.default_rng(seed).choice(
                len(pool), size=min(config.batch_size, len(pool)), replace=False
            )
            reports.append(
                evaluate_prepared_batch(
                    loaded,
                    profile,
                    pool.iloc[idx],
                    labels.iloc[idx].to_numpy(),
                    config=config,
                    config_dir=config_dir,
                    include_subgroups=False,
                    scores=all_scores[idx],
                )
            )
        return reports

    calibration = controls(
        "calibration", config.calibration_control_batches, config.calibration_control_seed
    )

    def rate(
        rows: list[dict[str, Any]], cfg: MonitoringConfig
    ) -> tuple[float, list[dict[str, Any]]]:
        evaluated = copy.deepcopy(rows)
        for report in evaluated:
            apply_alert_thresholds(report, config=cfg, baseline=profile.selection_metrics)
        return float(np.mean([row["alert"] for row in evaluated])), evaluated

    choices = {
        scale: rate(calibration, scaled_config(config, scale))[0]
        for scale in config.alert_scale_candidates
    }
    qualifying = [scale for scale, value in choices.items() if value <= config.false_alarm_target]
    if not qualifying:
        raise ValueError(f"no predeclared alert policy meets the calibration target: {choices}")
    scale = min(qualifying)
    frozen = scaled_config(config, scale)
    calibration_rate, _ = rate(calibration, frozen)
    validation = controls(
        "validation", config.validation_control_batches, config.validation_control_seed
    )
    validation_rate, evaluated = rate(validation, frozen)
    summary: dict[str, Any] = dict(
        model_version=loaded.version,
        model_run_id=loaded.run_id,
        model_sha256=profile.model_sha256,
        profile_hash=config_hash(profile.model_dump()),
        config_hash=config_hash(config.model_dump()),
        scale=scale,
        calibration_candidates=choices,
        calibration_batches=len(calibration),
        validation_batches=len(validation),
        calibration_false_alarm_rate=calibration_rate,
        validation_false_alarm_rate=validation_rate,
        validation_alert_features=dict(
            Counter(feature for row in evaluated for feature in row["alert_features"])
        ),
        validation_performance_alerts=dict(
            Counter(feature for row in evaluated for feature in row["performance_alerts"])
        ),
        target=config.false_alarm_target,
        passed=validation_rate <= config.false_alarm_target,
        sampling="independent seeded batches from one static Monitoring pool; batches can overlap",
        design=config.model_dump(),
    )
    with tracked_run(
        "risklab-monitoring-controls",
        loaded.version,
        {"model_run_id": loaded.run_id or "", "purpose": "monitoring-only"},
        config.model_dump(),
    ) as run_id:
        summary["run_id"] = run_id
        mlflow.log_metrics(
            {
                "calibration_false_alarm_rate": calibration_rate,
                "validation_false_alarm_rate": validation_rate,
                "alert_scale": scale,
            }
        )
        mlflow.log_dict(summary, "control_calibration.json")
        policy = AlertPolicy.model_validate({key: summary[key] for key in AlertPolicy.model_fields})
        path = reports_dir / "monitoring/alert-policy" / f"{profile.model_sha256}.json"
        write_json_atomic(path, policy.model_dump())
        mlflow.log_artifact(str(path), "alert-policy")
    write_json_atomic(root / "data/monitoring/manifest.json", {"batches": list(existing.values())})
    write_json_atomic(reports_dir / "monitoring" / f"controls-{profile.model_sha256}.json", summary)
    return summary


def load_candidate(path: Path) -> tuple[Any, ReferenceProfile]:
    """Read an already verified local candidate for monitoring; never train or open Test."""
    import pickle

    from risklab.api.model_store import LoadedModel
    from risklab.manifest import sha256_file

    candidate = json.loads(path.read_text())
    model_path = Path(candidate["model_path"])
    profile_path = Path(candidate["reference_profile_path"])
    if (
        sha256_file(model_path) != candidate["model_sha256"]
        or sha256_file(profile_path) != candidate["reference_profile_sha256"]
    ):
        raise ValueError("candidate model/profile hash changed")
    profile = ReferenceProfile.model_validate_json(profile_path.read_text())
    if profile.model_sha256 != candidate["model_sha256"]:
        raise ValueError("reference profile belongs to another candidate")
    with open(model_path, "rb") as fh:
        model = pickle.load(fh)
    loaded = LoadedModel(
        f"candidate-{candidate['model_sha256'][:12]}",
        model,
        candidate["threshold"],
        profile.contract_version,
        candidate["feature_groups"],
        run_id=candidate["selection_run_id"],
        reference_profile=profile,
    )
    return loaded, profile
