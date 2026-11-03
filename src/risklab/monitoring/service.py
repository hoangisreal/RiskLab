"""Manifest-registered simulations evaluated using the requested pinned model."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field, model_validator

from risklab.api.model_store import ModelStore, load_from_registry
from risklab.config import _Strict, load_data_config, load_model
from risklab.data.loaders import load_partition, partition_info
from risklab.features.historical import load_full_frame, load_unlabeled_frame
from risklab.manifest import sha256_file, write_json_atomic
from risklab.models.calibrate import predict_proba
from risklab.models.select import protocol_hashes
from risklab.models.threshold import load_cost_config
from risklab.monitoring.performance import performance_alerts
from risklab.monitoring.profile import ReferenceProfile, make_profile, profile_drift
from risklab.monitoring.report import evaluate_batch
from risklab.monitoring.simulate import Scenario, make_batch


class MonitoringConfig(_Strict):
    psi_warn: float = Field(ge=0)
    psi_alert: float = Field(gt=0)
    ks_alert: float = Field(gt=0, le=1)
    missing_rate_alert: float = Field(gt=0, le=1)
    false_alarm_target: float = Field(gt=0, lt=1)
    min_n: int = Field(ge=1)
    min_positives: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    seed: int
    reference_bins: int = Field(ge=2)
    reference_cdf_points: int = Field(ge=16)
    subgroup_bootstrap_resamples: int = Field(ge=100)
    subgroup_ci_level: float = Field(gt=0, lt=1)
    age_band_edges: list[float]
    performance_bootstrap_resamples: int = Field(ge=100)
    performance_ci_level: float = Field(gt=0, lt=1)
    performance_auc_drop: float = Field(ge=0)
    performance_log_loss_increase: float = Field(ge=0)
    performance_brier_increase: float = Field(ge=0)
    performance_min_auc: float = Field(ge=0, le=1)
    performance_max_log_loss: float = Field(gt=0)
    calibration_control_batches: int = Field(ge=20)
    validation_control_batches: int = Field(ge=20)
    calibration_control_seed: int
    validation_control_seed: int
    fault_income_multiplier: float = Field(gt=0)
    fault_ext3_missing_fraction: float = Field(ge=0, le=1)
    fault_label_flip_fraction: float = Field(ge=0, le=1)
    covariate_strength: float = Field(gt=0)
    mild_covariate_strength: float = Field(gt=0)
    alert_scale_candidates: list[float]

    @model_validator(mode="after")
    def valid_design(self) -> MonitoringConfig:
        if len(self.age_band_edges) < 2 or self.age_band_edges != sorted(set(self.age_band_edges)):
            raise ValueError("age band edges must be strictly increasing")
        a = range(
            self.calibration_control_seed,
            self.calibration_control_seed + self.calibration_control_batches,
        )
        b = range(
            self.validation_control_seed,
            self.validation_control_seed + self.validation_control_batches,
        )
        if max(a.start, b.start) < min(a.stop, b.stop):
            raise ValueError("calibration and validation control seeds must be disjoint")
        if (
            not self.alert_scale_candidates
            or self.alert_scale_candidates != sorted(set(self.alert_scale_candidates))
            or min(self.alert_scale_candidates) < 1
        ):
            raise ValueError("alert scales must be unique, increasing and at least one")
        return self


class BatchSpec(_Strict):
    batch_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    scenario: Scenario
    seed: int
    rows: int = Field(ge=1)


def subgroup_labels(frame: pd.DataFrame, *, age_edges: list[float] | None = None) -> dict[str, Any]:
    groups = {
        c: frame[c].fillna("unknown").astype(str).to_numpy()
        for c in (
            "CODE_GENDER",
            "NAME_INCOME_TYPE",
            "NAME_EDUCATION_TYPE",
            "REGION_RATING_CLIENT",
            "NAME_CONTRACT_TYPE",
        )
        if c in frame
    }
    if "DAYS_BIRTH" in frame:
        groups["age_band"] = (
            pd.cut(
                -frame["DAYS_BIRTH"] / 365.25, age_edges or [0, 25, 35, 45, 55, 65, float("inf")]
            )
            .astype(str)
            .to_numpy()
        )
    if "bureau_record_count" in frame:
        groups["has_bureau_history"] = (frame["bureau_record_count"] > 0).to_numpy()
    return groups


def registered_batches(root: Path) -> dict[str, Any]:
    path = root / "data" / "monitoring" / "manifest.json"
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    specs = [BatchSpec.model_validate(row) for row in doc["batches"]]
    if len({s.batch_id for s in specs}) != len(specs):
        raise ValueError("duplicate monitoring batch ids")
    return {s.batch_id: s.model_dump() for s in specs}


def run_monitoring(
    spec: BatchSpec,
    *,
    store: ModelStore,
    config_dir: Path,
    root: Path,
    model_version: str | None = None,
) -> dict[str, Any]:
    loaded = store.current
    if loaded is None:
        raise ValueError("no model loaded")
    if model_version is not None and model_version != loaded.version:
        loaded = load_from_registry(
            ModelStore(),
            version=model_version,
            expected_contract=loaded.contract_version,
            expected_protocol=protocol_hashes(root),
        )
    config = load_model(config_dir, "monitoring.yaml", MonitoringConfig)
    data_cfg = load_data_config(config_dir)
    if loaded.serving_config is not None:
        info = partition_info("train", config_dir=config_dir)
        if (
            info.partition_hash != loaded.serving_config.partition_hash
            or sha256_file(data_cfg.manifest_path) != loaded.serving_config.data_manifest_hash
            or protocol_hashes(root) != loaded.serving_config.protocol_hashes
        ):
            raise ValueError("monitoring data/config differs from the frozen model reference")
    profile = loaded.reference_profile
    columns = list(loaded.model.feature_names_in_)
    if profile is None:
        if loaded.serving_config is not None:
            raise ValueError("registered model is missing its immutable reference profile")
        reference, _ = load_full_frame(
            "train", "fit", loaded.feature_groups, config_dir=config_dir, root=root
        )
        selection, y_selection = load_full_frame(
            "selection", "select", loaded.feature_groups, config_dir=config_dir, root=root
        )
        profile = make_profile(
            reference[columns],
            predict_proba(loaded.model, selection[columns]),
            model_sha256="synthetic-local-artifact",
            contract_version=loaded.contract_version,
            bins=config.reference_bins,
            cdf_points=config.reference_cdf_points,
            selection_labels=y_selection.to_numpy(),
        )
    monitor_groups = list(dict.fromkeys([*loaded.feature_groups, "bureau"]))
    if spec.scenario == "kaggle_unlabeled":
        batch = load_unlabeled_frame(
            monitor_groups, config_dir=config_dir, root=root, sample_size=spec.rows, seed=spec.seed
        ).reset_index(drop=True)
        y = None
    else:
        pool, labels = load_monitoring_pool(monitor_groups, config_dir=config_dir, root=root)
        strength = (
            config.mild_covariate_strength
            if spec.scenario == "covariate_young_mild"
            else config.covariate_strength
        )
        batch, labels_batch = make_batch(
            pool,
            labels,
            scenario=spec.scenario,
            batch_size=spec.rows,
            seed=spec.seed,
            income_multiplier=config.fault_income_multiplier,
            missing_fraction=config.fault_ext3_missing_fraction,
            flip_fraction=config.fault_label_flip_fraction,
            covariate_strength=strength,
        )
        y = labels_batch.to_numpy()
        if spec.scenario == "bug_income_x1000":
            batch = refresh_application_ratios(batch, config_dir=config_dir, root=root)
    from risklab.monitoring.policy import effective_config

    config, policy_run = effective_config(config, profile, reports_dir=data_cfg.reports_dir)
    report = evaluate_prepared_batch(
        loaded, profile, batch, y, config=config, config_dir=config_dir
    )
    report.update(
        batch_id=spec.batch_id,
        scenario=spec.scenario,
        seed=spec.seed,
        model_version=loaded.version,
        model_run_id=loaded.run_id,
        lock_id=loaded.lock_id,
        alert_policy_run_id=policy_run,
    )
    if os.environ.get("MLFLOW_TRACKING_URI"):
        import mlflow

        from risklab.tracking import tracked_run

        with tracked_run(
            "risklab-monitoring",
            spec.batch_id,
            {"model_version": loaded.version, "model_run_id": loaded.run_id or ""},
            spec.model_dump(),
        ) as run_id:
            report["run_id"] = run_id
            mlflow.log_dict(report, "report.json")
    write_json_atomic(
        data_cfg.reports_dir / "monitoring" / f"{spec.batch_id}-{loaded.version}.json", report
    )
    return report


def load_monitoring_pool(
    groups: list[str], *, config_dir: Path, root: Path
) -> tuple[pd.DataFrame, pd.Series]:
    """Join descriptive metadata by applicant id; labels still use the sole role-gated loader."""
    pool, labels = load_full_frame(
        "monitoring", "monitor", groups, config_dir=config_dir, root=root
    )
    from risklab.config import load_raw_schema
    from risklab.data.clean import clean_application, load_cleaning_config

    table = load_partition("monitoring", "monitor", config_dir=config_dir)
    cleaning = load_cleaning_config(config_dir, load_raw_schema(config_dir))
    metadata = clean_application(table, cleaning).to_pandas().set_index("SK_ID_CURR")
    if "CODE_GENDER" in metadata:
        pool = pool.assign(CODE_GENDER=metadata["CODE_GENDER"].reindex(pool.index))
    return pool, labels


def refresh_application_ratios(
    batch: pd.DataFrame, *, config_dir: Path, root: Path
) -> pd.DataFrame:
    """A raw upstream mutation must flow through the same row-wise ratio SQL."""
    import numpy as np
    import pyarrow as pa

    from risklab.data.duckdb_utils import duckdb_connect
    from risklab.features.registry import load_registry

    cfg = load_data_config(config_dir)
    frame = batch.copy()
    frame["SK_ID_CURR"] = np.arange(len(frame))
    con = duckdb_connect(cfg.duckdb)
    try:
        table = pa.Table.from_pandas(frame, preserve_index=False)
        con.register("application_src", table)
        con.register("applicants", table.select(["SK_ID_CURR"]))
        registry = load_registry(config_dir)
        ratios = con.sql((root / registry.groups["application"].source_sql).read_text()).to_df()
        ratios = ratios.set_index("SK_ID_CURR").reindex(np.arange(len(frame)))
    finally:
        con.close()
    result = batch.copy()
    for name in ratios:
        result[name] = ratios[name].to_numpy()
    return result


def evaluate_prepared_batch(
    loaded: Any,
    profile: ReferenceProfile,
    batch: pd.DataFrame,
    y: Any,
    *,
    config: MonitoringConfig,
    config_dir: Path,
    include_subgroups: bool = True,
    scores: Any = None,
) -> dict[str, Any]:
    """One engine shared by registered reports and the control calibration protocol."""
    columns = list(loaded.model.feature_names_in_)
    if scores is None:
        scores = predict_proba(loaded.model, batch[columns])
    costs = loaded.serving_config.cost if loaded.serving_config else load_cost_config(config_dir)
    fp, fn, exposure = costs.cost_fp, costs.cost_fn, None
    if costs.policy == "per_loan":
        assert costs.loan is not None
        fp, fn = costs.loan.margin, costs.loan.lgd
        exposure = batch["AMT_CREDIT"].to_numpy(dtype=float)
    report = evaluate_batch(
        pd.DataFrame(),
        batch[columns],
        y,
        scores,
        None,
        threshold=loaded.threshold,
        groups=subgroup_labels(batch, age_edges=config.age_band_edges)
        if include_subgroups
        else None,
        psi_warn=config.psi_warn,
        psi_alert=config.psi_alert,
        ks_alert=config.ks_alert,
        missing_rate_alert=config.missing_rate_alert,
        min_n=config.min_n,
        min_positives=config.min_positives,
        seed=config.seed,
        cost_fp=fp,
        cost_fn=fn,
        exposure=exposure,
        subgroup_resamples=config.subgroup_bootstrap_resamples,
        subgroup_ci_level=config.subgroup_ci_level,
        performance_resamples=config.performance_bootstrap_resamples,
        performance_ci_level=config.performance_ci_level,
    )
    report.update(
        profile_drift(
            profile,
            batch[columns],
            scores,
            psi_warn=config.psi_warn,
            psi_alert=config.psi_alert,
            ks_alert=config.ks_alert,
            missing_rate_alert=config.missing_rate_alert,
        )
    )
    apply_alert_thresholds(report, config=config, baseline=profile.selection_metrics)
    report["labels_available"] = y is not None
    return report


def apply_alert_thresholds(
    report: dict[str, Any], *, config: MonitoringConfig, baseline: dict[str, float]
) -> None:
    """Apply a frozen policy to existing statistics without recomputing or resampling them."""
    for row in report["drift"]:
        alert = (
            row["psi"] >= config.psi_alert
            or row["ks"] >= config.ks_alert
            or abs(row["missing_rate_change"]) >= config.missing_rate_alert
        )
        row["status"] = "alert" if alert else ("warn" if row["psi"] >= config.psi_warn else "ok")
    report["alert_features"] = [
        row["feature"] for row in report["drift"] if row["status"] == "alert"
    ]
    report["score_alert"] = report["score_psi"] >= config.psi_alert
    report["performance_alerts"] = []
    if "performance" in report and baseline:
        report["performance_alerts"] = performance_alerts(
            report["performance"],
            baseline,
            auc_drop=config.performance_auc_drop,
            log_loss_increase=config.performance_log_loss_increase,
            brier_increase=config.performance_brier_increase,
            min_auc=config.performance_min_auc,
            max_log_loss=config.performance_max_log_loss,
        )
    report["alert"] = bool(
        report["alert_features"] or report["score_alert"] or report["performance_alerts"]
    )
    report["effective_alert_thresholds"] = {
        key: getattr(config, key)
        for key in (
            "psi_alert",
            "ks_alert",
            "missing_rate_alert",
            "performance_auc_drop",
            "performance_log_loss_increase",
            "performance_brier_increase",
            "performance_min_auc",
            "performance_max_log_loss",
            "performance_ci_level",
        )
    }
