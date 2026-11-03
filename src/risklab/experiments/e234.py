"""E2/E3/E4 runners (MASTER_PLAN Section 6): feature groups, calibration, threshold.

All decisions use Selection only; Test is never read. Each runner returns a summary
dict with MLflow run ids when tracking is enabled (empty without tracking).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field

from risklab.config import (
    DataConfig,
    PartitionConfig,
    _Strict,
    load_data_config,
    load_model,
    load_partition_config,
)
from risklab.data.loaders import PartitionRole, Purpose, partition_info
from risklab.features.historical import load_full_frame
from risklab.features.registry import load_registry
from risklab.models.calibrate import (
    fit_calibrator,
    load_calibration_config,
    predict_proba,
    select_calibration_method,
)
from risklab.models.evaluate import compute_metrics, paired_bootstrap
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.threshold import choose_threshold, load_cost_config, sensitivity
from risklab.models.train import fit_pipeline
from risklab.tracking import provenance_tags, tracked_run


class E2Config(_Strict):
    experiment_name: str
    seed: int = 20261101
    min_gain: float = Field(ge=0)
    bootstrap_resamples: int = Field(ge=100)
    bootstrap_seed: int = 20261101
    ci_level: float = Field(gt=0, lt=1)
    ablation_order: list[str]


class E3Config(_Strict):
    experiment_name: str
    seed: int = 20261101
    methods: list[str]
    primary_metric: str = "log_loss"
    bootstrap_resamples: int = Field(ge=100)
    bootstrap_seed: int = 20261101
    ci_level: float = Field(gt=0, lt=1)
    include_scale_pos_weight_diagnostic: bool = True


class E4Config(_Strict):
    experiment_name: str
    seed: int = 20261101
    cost_fp: float = Field(gt=0)
    cost_fn: float = Field(gt=0)
    threshold_grid_size: int = Field(ge=10)
    sensitivity_ratios: list[float]


def _log_experiment(
    experiment: str, root: Path, summary: dict[str, Any], flat_metrics: dict[str, float]
) -> str:
    """Log one MLflow run for an experiment summary; returns the run id."""
    import mlflow

    tags = provenance_tags(
        root=root,
        partition_hash=str(summary.get("partition_hash", "")),
        config={},
        extra={"experiment": experiment},
    )
    with tracked_run(experiment, "summary", tags, {}) as run_id:
        mlflow.log_metrics(flat_metrics)
        mlflow.log_dict(summary, "summary.json")
    return run_id


def _frames(
    groups: list[str],
    *,
    config_dir: Any,
    data_cfg: DataConfig,
    partition_cfg: PartitionConfig,
    root: Path,
) -> dict[str, tuple[pd.DataFrame, pd.Series]]:
    out = {}
    for role, purpose in (
        (PartitionRole.train, Purpose.fit),
        (PartitionRole.calibration, Purpose.calibrate),
        (PartitionRole.selection, Purpose.select),
    ):
        out[role.value] = load_full_frame(
            role,
            purpose,
            groups,
            data_cfg=data_cfg,
            partition_cfg=partition_cfg,
            config_dir=config_dir,
            root=root,
        )
    return out


def run_e2(
    *,
    config_dir: Any,
    root: Path,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    cfg: E2Config = load_model(config_dir, "experiments/e2.yaml", E2Config)
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    registry = load_registry(config_dir)
    all_groups = [g for g in cfg.ablation_order if g != "application"]
    frames = _frames(
        all_groups, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg, root=root
    )
    from risklab.features.application import feature_spec, load_features_config

    spec = feature_spec(
        load_features_config(config_dir),
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    lgbm_base = load_lgbm_params(config_dir)
    X_tr, y_tr = frames["train"]
    X_se, y_se = frames["selection"]
    y_sel = y_se.to_numpy()
    results: list[dict[str, Any]] = []
    ref_scores: np.ndarray | None = None
    prev_scores: np.ndarray | None = None
    cumulative: list[str] = []
    for group in cfg.ablation_order:
        if group != "application":
            cumulative.append(group)
        groups_now = list(cumulative)
        hist_cols = registry.group_features(groups_now)
        from risklab.features.application import FeatureColumns

        cols = FeatureColumns(
            numeric=list(spec.numeric) + hist_cols,
            categorical=list(spec.categorical),
            log=list(spec.log),
        )
        pipe = fit_pipeline(
            make_lgbm_pipeline(
                lgbm_base, columns=cols, config_dir=str(config_dir) if config_dir else None
            ),
            X_tr[cols.all],
            y_tr,
        )
        scores = predict_proba(pipe, X_se[cols.all])
        metrics = compute_metrics(y_sel, scores)
        entry: dict[str, Any] = {
            "step": group,
            "groups": ["application"] + groups_now,
            "metrics": metrics,
        }
        if ref_scores is None:
            ref_scores = scores
            prev_scores = scores
            entry["vs_application"] = None
        else:
            boot_app = paired_bootstrap(
                y_sel,
                scores,
                ref_scores,
                n_resamples=cfg.bootstrap_resamples,
                seed=cfg.bootstrap_seed,
                ci_level=cfg.ci_level,
                metrics=["roc_auc"],
            )["roc_auc"]
            # Marginal gain of this step is against the previous cumulative set,
            # not against application-only (that is reported separately).
            assert prev_scores is not None
            boot_marginal = paired_bootstrap(
                y_sel,
                scores,
                prev_scores,
                n_resamples=cfg.bootstrap_resamples,
                seed=cfg.bootstrap_seed,
                ci_level=cfg.ci_level,
                metrics=["roc_auc"],
            )["roc_auc"]
            entry["vs_application"] = boot_app
            entry["marginal"] = boot_marginal
            entry["marginal_gain"] = boot_marginal["delta"]
            entry["served_by_rule"] = bool(
                boot_marginal["delta"] >= cfg.min_gain and boot_marginal["ci_low"] > 0
            )
            prev_scores = scores
        results.append(entry)
        log(f"E2 {group}: ROC-AUC {metrics['roc_auc']:.5f}")
    served = ["application"] + [r["groups"][-1] for r in results[1:] if r.get("served_by_rule")]
    summary: dict[str, Any] = {
        "experiment": cfg.experiment_name,
        "partition_hash": partition_info(
            PartitionRole.train,
            data_cfg=data_cfg,
            partition_cfg=partition_cfg,
            config_dir=config_dir,
        ).partition_hash,
        "min_gain": cfg.min_gain,
        "ablation": results,
        "served_groups": served,
    }
    try:
        summary["run_id"] = _log_experiment(
            cfg.experiment_name,
            root,
            summary,
            {f"{r['step']}_roc_auc": r["metrics"]["roc_auc"] for r in results},
        )
    except Exception as exc:  # tracking optional; summary still records the failure
        summary["run_id"] = None
        summary["tracking_error"] = str(exc)
    return summary


def run_e3(
    *,
    config_dir: Any,
    root: Path,
    groups: list[str] | None = None,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    cfg: E3Config = load_model(config_dir, "experiments/e3.yaml", E3Config)
    cal_cfg = load_calibration_config(config_dir)
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    registry = load_registry(config_dir)
    groups = (
        groups if groups is not None else [g for g in registry.all_groups() if g != "application"]
    )
    frames = _frames(
        groups, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg, root=root
    )
    from risklab.features.application import FeatureColumns, feature_spec, load_features_config

    spec = feature_spec(
        load_features_config(config_dir),
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    hist_cols = registry.group_features(groups)
    cols = FeatureColumns(
        numeric=list(spec.numeric) + hist_cols,
        categorical=list(spec.categorical),
        log=list(spec.log),
    )
    lgbm_base = load_lgbm_params(config_dir)
    X_tr, y_tr = frames["train"]
    X_cal, y_cal = frames["calibration"]
    X_se, y_se = frames["selection"]
    base = fit_pipeline(
        make_lgbm_pipeline(
            lgbm_base, columns=cols, config_dir=str(config_dir) if config_dir else None
        ),
        X_tr[cols.all],
        y_tr,
    )
    scores: dict[str, np.ndarray] = {}
    for method in cfg.methods:
        model = fit_calibrator(base, X_cal[cols.all], y_cal, method)
        scores[method] = predict_proba(model, X_se[cols.all])
    selection = select_calibration_method(y_se.to_numpy(), scores, config=cal_cfg)
    log(f"E3 chosen calibration: {selection['chosen']}")
    out: dict[str, Any] = {
        "experiment": cfg.experiment_name,
        "methods": cfg.methods,
        "selection": selection,
    }
    if cfg.include_scale_pos_weight_diagnostic:
        # Diagnostic only: class weighting distorts probabilities (AGENTS.md rule 5).
        ytr = y_tr.to_numpy()
        spw = float((ytr == 0).sum() / max((ytr == 1).sum(), 1))
        diag_params = lgbm_base.model_copy(update={})
        pipe = make_lgbm_pipeline(
            diag_params, columns=cols, config_dir=str(config_dir) if config_dir else None
        )
        pipe.named_steps["model"].set_params(scale_pos_weight=spw)
        fitted = fit_pipeline(pipe, X_tr[cols.all], y_tr)
        out["scale_pos_weight_diagnostic"] = {
            "scale_pos_weight": spw,
            "selection_metrics_uncalibrated": compute_metrics(
                y_se.to_numpy(), predict_proba(fitted, X_se[cols.all])
            ),
            "note": "diagnostic only; probabilities are distorted by class weighting",
        }
    try:
        out["run_id"] = _log_experiment(
            cfg.experiment_name,
            root,
            out,
            {f"{m}_log_loss": selection["metrics"][m]["log_loss"] for m in cfg.methods},
        )
    except Exception as exc:
        out["run_id"] = None
        out["tracking_error"] = str(exc)
    return out


def run_e4(
    *,
    config_dir: Any,
    root: Path,
    groups: list[str] | None = None,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    calibration_method: str = "sigmoid",
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    cfg: E4Config = load_model(config_dir, "experiments/e4.yaml", E4Config)
    cost = load_cost_config(config_dir)
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    registry = load_registry(config_dir)
    groups = (
        groups if groups is not None else [g for g in registry.all_groups() if g != "application"]
    )
    frames = _frames(
        groups, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg, root=root
    )
    from risklab.features.application import FeatureColumns, feature_spec, load_features_config

    spec = feature_spec(
        load_features_config(config_dir),
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    hist_cols = registry.group_features(groups)
    cols = FeatureColumns(
        numeric=list(spec.numeric) + hist_cols,
        categorical=list(spec.categorical),
        log=list(spec.log),
    )
    lgbm_base = load_lgbm_params(config_dir)
    X_tr, y_tr = frames["train"]
    X_cal, y_cal = frames["calibration"]
    X_se, y_se = frames["selection"]
    base = fit_pipeline(
        make_lgbm_pipeline(
            lgbm_base, columns=cols, config_dir=str(config_dir) if config_dir else None
        ),
        X_tr[cols.all],
        y_tr,
    )
    model = fit_calibrator(base, X_cal[cols.all], y_cal, calibration_method)
    scores = predict_proba(model, X_se[cols.all])
    choice = choose_threshold(
        y_se.to_numpy(),
        scores,
        cost_fp=cfg.cost_fp,
        cost_fn=cfg.cost_fn,
        grid_size=cfg.threshold_grid_size,
    )
    sens = sensitivity(
        y_se.to_numpy(),
        scores,
        cost_fp=cfg.cost_fp,
        ratios=[float(r) for r in cfg.sensitivity_ratios],
        grid_size=cfg.threshold_grid_size,
    )
    log(f"E4 threshold {choice['threshold']:.4f} (Bayes {choice['bayes_threshold']:.4f})")
    out4: dict[str, Any] = {
        "experiment": cfg.experiment_name,
        "partition_hash": partition_info(
            PartitionRole.train,
            data_cfg=data_cfg,
            partition_cfg=partition_cfg,
            config_dir=config_dir,
        ).partition_hash,
        "calibration_method": calibration_method,
        "threshold_choice": {k: v for k, v in choice.items() if k != "curve"},
        "sensitivity": sens,
        "cost_config": cost.model_dump(mode="json"),
    }
    try:
        out4["run_id"] = _log_experiment(
            cfg.experiment_name, root, out4, {"threshold": float(choice["threshold"])}
        )
    except Exception as exc:
        out4["run_id"] = None
        out4["tracking_error"] = str(exc)
    return out4


__all__ = ["E2Config", "E3Config", "E4Config", "run_e2", "run_e3", "run_e4"]


def run_policy_analysis(
    *,
    config_dir: Any,
    root: Path,
    groups: list[str] | None = None,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
) -> dict[str, Any]:
    """Sensitive-attribute policy check on Selection: with vs without CODE_GENDER.

    Default policy (plan Section 6) excludes CODE_GENDER. This compares Selection
    ROC-AUC/log loss of the full pipeline with the gender column added back as a
    categorical feature. The decision recorded in PROGRESS.md is to keep the
    exclusion unless this shows a material gain and the owner approves inclusion.
    """
    from risklab.data.clean import clean_application, load_cleaning_config
    from risklab.data.loaders import load_partition
    from risklab.features.application import FeatureColumns, feature_spec, load_features_config

    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    registry = load_registry(config_dir)
    groups = (
        groups if groups is not None else [g for g in registry.all_groups() if g != "application"]
    )
    frames = _frames(
        groups, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg, root=root
    )
    spec = feature_spec(
        load_features_config(config_dir),
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    hist_cols = registry.group_features(groups)
    lgbm_base = load_lgbm_params(config_dir)
    X_tr, y_tr = frames["train"]
    X_se, y_se = frames["selection"]

    def _with_gender(role: str, purpose: str, X: pd.DataFrame) -> pd.DataFrame:
        from risklab.config import load_raw_schema

        schema = load_raw_schema(config_dir, data_cfg=data_cfg)
        cleaning = load_cleaning_config(config_dir, schema)
        table = load_partition(
            role, purpose, data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=config_dir
        )
        cleaned = clean_application(table, cleaning)
        vals = cleaned["CODE_GENDER"].to_pylist()
        out = X.copy()
        out["CODE_GENDER"] = [float("nan") if v is None else v for v in vals]
        return out

    results: dict[str, Any] = {}
    for label, add_gender in (("excluded", False), ("included", True)):
        cols = FeatureColumns(
            numeric=list(spec.numeric) + hist_cols,
            categorical=list(spec.categorical) + (["CODE_GENDER"] if add_gender else []),
            log=list(spec.log),
        )
        Xtr = _with_gender("train", "fit", X_tr) if add_gender else X_tr
        Xse = _with_gender("selection", "select", X_se) if add_gender else X_se
        pipe = fit_pipeline(
            make_lgbm_pipeline(
                lgbm_base, columns=cols, config_dir=str(config_dir) if config_dir else None
            ),
            Xtr[cols.all],
            y_tr,
        )
        results[label] = compute_metrics(y_se.to_numpy(), predict_proba(pipe, Xse[cols.all]))
    return {"policy": "exclude CODE_GENDER (default)", "selection_metrics": results}
