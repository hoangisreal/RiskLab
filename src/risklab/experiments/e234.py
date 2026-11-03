"""E2/E3/E4 runners (MASTER_PLAN Section 6): feature groups, calibration, threshold.

All decisions use Selection only; Test is never read. Each runner returns a summary
dict with MLflow run ids when tracking is enabled (empty without tracking).
"""

from __future__ import annotations

import pickle
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
from risklab.experiments.artifacts import analysis_manifest, calibration_plot, threshold_plot
from risklab.features.historical import load_full_frame
from risklab.features.registry import load_registry
from risklab.manifest import library_versions, sha256_file
from risklab.models.calibrate import (
    fit_calibrator,
    load_calibration_config,
    predict_proba,
    select_calibration_method,
)
from risklab.models.evaluate import compute_metrics, paired_bootstrap, save_predictions
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.select import protocol_hashes
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
        config=summary.get("resolved_config", {}),
        extra={"experiment": experiment},
    )
    with tracked_run(experiment, "summary", tags, summary.get("resolved_config", {})) as run_id:
        summary["run_id"] = run_id
        mlflow.log_metrics(flat_metrics)
        mlflow.log_dict(summary, "summary.json")
        for key in ("model_path", "predictions_path"):
            if summary.get(key):
                mlflow.log_artifact(str(summary[key]))
        for path in summary.get("artifacts", {}).values():
            mlflow.log_artifact(str(path))
    return run_id


def _frames(
    groups: list[str],
    *,
    config_dir: Any,
    data_cfg: DataConfig,
    partition_cfg: PartitionConfig,
    root: Path,
    calibration: bool = True,
) -> dict[str, tuple[pd.DataFrame, pd.Series]]:
    out = {}
    roles = [(PartitionRole.train, Purpose.fit), (PartitionRole.selection, Purpose.select)]
    if calibration:
        roles.insert(1, (PartitionRole.calibration, Purpose.calibrate))
    for role, purpose in roles:
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
        all_groups,
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        root=root,
        calibration=False,
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
    prediction_scores: dict[str, np.ndarray] = {}
    ref_scores: np.ndarray | None = None
    prev_scores: np.ndarray | None = None
    cumulative: list[str] = []
    for group in cfg.ablation_order:
        if group != "application":
            cumulative.append(group)
        groups_now = list(cumulative)
        hist_cols = registry.group_features(["application", *groups_now])
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
        prediction_scores[group] = scores
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
    leave_one_out = []
    assert prev_scores is not None
    for removed in all_groups:
        kept = [g for g in all_groups if g != removed]
        columns = FeatureColumns(
            numeric=list(spec.numeric) + registry.group_features(["application", *kept]),
            categorical=list(spec.categorical),
            log=list(spec.log),
        )
        without = fit_pipeline(
            make_lgbm_pipeline(
                lgbm_base, columns=columns, config_dir=str(config_dir) if config_dir else None
            ),
            X_tr[columns.all],
            y_tr,
        )
        without_scores = predict_proba(without, X_se[columns.all])
        prediction_scores[f"without_{removed}"] = without_scores
        leave_one_out.append(
            {
                "removed_group": removed,
                "metrics_without": compute_metrics(y_sel, without_scores),
                "full_minus_without": paired_bootstrap(
                    y_sel,
                    prev_scores,
                    without_scores,
                    n_resamples=cfg.bootstrap_resamples,
                    seed=cfg.bootstrap_seed,
                    ci_level=cfg.ci_level,
                    metrics=["roc_auc"],
                )["roc_auc"],
            }
        )
    predictions = save_predictions(
        data_cfg.reports_dir / "experiments/e2/selection_predictions.parquet",
        X_se.index,
        y_sel,
        prediction_scores,
    )
    summary: dict[str, Any] = {
        "predictions_path": str(predictions),
        "experiment": cfg.experiment_name,
        "partition_hash": partition_info(
            PartitionRole.train,
            data_cfg=data_cfg,
            partition_cfg=partition_cfg,
            config_dir=config_dir,
        ).partition_hash,
        "resolved_config": cfg.model_dump(),
        "contract_version": registry.contract_version,
        "data_manifest_hash": sha256_file(data_cfg.manifest_path),
        "protocol_hashes": protocol_hashes(root),
        "min_gain": cfg.min_gain,
        "ablation": results,
        "served_groups": served,
        # Descriptive robustness check; does not alter the pre-declared cumulative rule.
        "leave_one_group_out": leave_one_out,
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
    analysis_manifest("e2", summary, data_cfg=data_cfg, root=root)
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
    hist_cols = registry.group_features(["application", *groups])
    cols = FeatureColumns(
        numeric=list(spec.numeric) + hist_cols,
        categorical=list(spec.categorical),
        log=list(spec.log),
    )
    lgbm_base = load_lgbm_params(config_dir)
    X_tr, y_tr = frames["train"]
    X_cal, y_cal = frames["calibration"]
    X_se, y_se = frames["selection"]
    import json

    e1 = json.loads((data_cfg.reports_dir / "experiments/e1_served/summary.json").read_text())
    winner = e1["decision"]["preferred"]
    if (
        e1["feature_groups"] != groups
        or e1["protocol_hashes"] != protocol_hashes(root)
        or e1["data_manifest_hash"] != sha256_file(data_cfg.manifest_path)
    ):
        raise ValueError("served E1 artifact no longer matches the data/config/source set")
    if not e1["run_ids"].get(winner):
        raise ValueError("a tracked E1 --served decision is required before calibration")
    base_path = Path(e1["model_paths"][winner])
    if sha256_file(base_path) != e1["model_sha256"][winner]:
        raise ValueError("selected E1 artifact changed")
    with open(base_path, "rb") as fh:
        base = pickle.load(fh)
    scores: dict[str, np.ndarray] = {}
    models: dict[str, Any] = {}
    for method in cfg.methods:
        model = fit_calibrator(base, X_cal[cols.all], y_cal, method)
        models[method] = model
        scores[method] = predict_proba(model, X_se[cols.all])
    selection = select_calibration_method(y_se.to_numpy(), scores, config=cal_cfg)
    log(f"E3 chosen calibration: {selection['chosen']}")
    output = data_cfg.reports_dir / "experiments" / "e3"
    output.mkdir(parents=True, exist_ok=True)
    import hashlib

    model_bytes = pickle.dumps(models[selection["chosen"]])
    model_path = output / f"selected-{hashlib.sha256(model_bytes).hexdigest()}.pkl"
    if not model_path.exists():
        model_path.write_bytes(model_bytes)
    predictions = save_predictions(
        output / "selection_predictions.parquet", X_se.index, y_se.to_numpy(), scores
    )
    plot = calibration_plot(
        y_se.to_numpy(), scores, output / "selection_reliability.png", bins=cal_cfg.ece_bins
    )
    out: dict[str, Any] = {
        "artifacts": {"reliability": str(plot)},
        "contract_version": registry.contract_version,
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "predictions_path": str(predictions),
        "feature_groups": groups,
        "feature_columns": cols.all,
        "library_versions": library_versions(),
        "protocol_hashes": protocol_hashes(root),
        "data_manifest_hash": sha256_file(data_cfg.manifest_path),
        "resolved_config": {
            "experiment": cfg.model_dump(),
            "calibration": cal_cfg.model_dump(),
            "model": lgbm_base.model_dump(),
        },
        "partition_hash": partition_info(
            "train", data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=config_dir
        ).partition_hash,
        "experiment": cfg.experiment_name,
        "methods": cfg.methods,
        "base_model_family": winner,
        "base_model_run_id": e1["run_ids"][winner],
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
    analysis_manifest("calibrate", out, data_cfg=data_cfg, root=root)
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
    from risklab.features.application import FeatureColumns, feature_spec, load_features_config

    spec = feature_spec(
        load_features_config(config_dir),
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    hist_cols = registry.group_features(["application", *groups])
    cols = FeatureColumns(
        numeric=list(spec.numeric) + hist_cols,
        categorical=list(spec.categorical),
        log=list(spec.log),
    )
    import json

    e3_path = data_cfg.reports_dir / "experiments" / "e3" / "summary.json"
    e3 = json.loads(e3_path.read_text(encoding="utf-8"))
    if e3.get("feature_groups") != groups or e3["selection"]["chosen"] != calibration_method:
        raise ValueError("E4 must use the selected E3 feature set and calibration method")
    if e3.get("protocol_hashes") != protocol_hashes(root) or e3.get(
        "data_manifest_hash"
    ) != sha256_file(data_cfg.manifest_path):
        raise ValueError("E3 data/config changed before threshold selection")
    model_path = Path(e3["model_path"])
    if sha256_file(model_path) != e3["model_sha256"]:
        raise ValueError("selected E3 artifact changed")
    with open(model_path, "rb") as fh:
        model = pickle.load(fh)
    X_se, y_se = load_full_frame(
        "selection",
        "select",
        groups,
        config_dir=config_dir,
        root=root,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    scores = predict_proba(model, X_se[cols.all])
    choice = choose_threshold(
        y_se.to_numpy(),
        scores,
        cost_fp=cfg.cost_fp,
        cost_fn=cfg.cost_fn,
        grid_size=cfg.threshold_grid_size,
    )
    constant_choice = choice
    loan_sensitivity = []
    if cost.policy == "per_loan":
        assert cost.loan is not None
        exposures = X_se["AMT_CREDIT"].to_numpy(dtype=float)
        choice = choose_threshold(
            y_se.to_numpy(),
            scores,
            cost_fp=cost.loan.margin,
            cost_fn=cost.loan.lgd,
            grid_size=cost.threshold_grid_size,
            exposure=exposures,
        )
        for lgd in cost.loan.sensitivity_lgd:
            for margin in cost.loan.sensitivity_margin:
                analysis = choose_threshold(
                    y_se.to_numpy(),
                    scores,
                    cost_fp=margin,
                    cost_fn=lgd,
                    grid_size=cost.threshold_grid_size,
                    exposure=exposures,
                )
                loan_sensitivity.append(
                    {
                        "lgd": lgd,
                        "margin": margin,
                        "threshold": analysis["threshold"],
                        "cost": analysis["best"],
                    }
                )
    sens = sensitivity(
        y_se.to_numpy(),
        scores,
        cost_fp=cfg.cost_fp,
        ratios=[float(r) for r in cfg.sensitivity_ratios],
        grid_size=cfg.threshold_grid_size,
    )
    log(f"E4 threshold {choice['threshold']:.4f} (Bayes {choice['bayes_threshold']:.4f})")
    output = data_cfg.reports_dir / "experiments/e4"
    output.mkdir(parents=True, exist_ok=True)
    curve = pd.DataFrame(choice["curve"])
    curve_path = output / "selection_cost_curve.parquet"
    curve.to_parquet(curve_path, index=False)
    plot = threshold_plot(curve, output / "selection_cost_curve.png")
    out4: dict[str, Any] = {
        "contract_version": registry.contract_version,
        "protocol_hashes": protocol_hashes(root),
        "data_manifest_hash": sha256_file(data_cfg.manifest_path),
        "feature_groups": groups,
        "model_path": str(model_path),
        "model_sha256": e3["model_sha256"],
        "artifacts": {"cost_curve": str(curve_path), "cost_plot": str(plot)},
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
        "cost_policy": cost.policy,
        "constant_threshold_choice": {k: v for k, v in constant_choice.items() if k != "curve"},
        "loan_sensitivity": loan_sensitivity,
        "resolved_config": cfg.model_dump(),
    }
    try:
        out4["run_id"] = _log_experiment(
            cfg.experiment_name, root, out4, {"threshold": float(choice["threshold"])}
        )
    except Exception as exc:
        out4["run_id"] = None
        out4["tracking_error"] = str(exc)
    analysis_manifest("select", out4, data_cfg=data_cfg, root=root)
    return out4


__all__ = ["E2Config", "E3Config", "E4Config", "run_e2", "run_e3", "run_e4"]


class PolicyConfig(_Strict):
    experiment_name: str
    seed: int
    bootstrap_resamples: int = Field(ge=100)
    bootstrap_seed: int
    ci_level: float = Field(gt=0, lt=1)
    gender_column: str
    age_columns: list[str]
    default_policy: str


def run_policy_analysis(
    *,
    config_dir: Any,
    root: Path,
    groups: list[str] | None = None,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
) -> dict[str, Any]:
    """Descriptive fixed-LightGBM gender/age ablations; never change the feature policy."""
    from risklab.config import load_raw_schema
    from risklab.data.clean import clean_application, load_cleaning_config
    from risklab.data.loaders import load_partition
    from risklab.features.application import FeatureColumns, feature_spec, load_features_config

    cfg = load_model(config_dir, "experiments/policy.yaml", PolicyConfig)
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    registry = load_registry(config_dir)
    groups = (
        groups if groups is not None else [g for g in registry.all_groups() if g != "application"]
    )
    frames = _frames(
        groups,
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        root=root,
        calibration=False,
    )
    spec = feature_spec(
        load_features_config(config_dir),
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    numeric = list(spec.numeric) + registry.group_features(["application", *groups])
    categorical = list(spec.categorical)
    lgbm = load_lgbm_params(config_dir)
    schema = load_raw_schema(config_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(config_dir, schema)
    for role, purpose in (("train", "fit"), ("selection", "select")):
        frame, _ = frames[role]
        cleaned = clean_application(
            load_partition(
                role, purpose, data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=config_dir
            ),
            cleaning,
        )
        metadata = cleaned.select([partition_cfg.id_column, cfg.gender_column]).to_pandas()
        metadata = metadata.set_index(partition_cfg.id_column)
        frame[cfg.gender_column] = metadata[cfg.gender_column].reindex(frame.index).astype(object)
        frame[cfg.gender_column] = frame[cfg.gender_column].where(
            frame[cfg.gender_column].notna(), np.nan
        )
    X_tr, y_tr = frames["train"]
    X_se, y_se = frames["selection"]
    if not set(cfg.age_columns) <= set(numeric):
        raise ValueError("configured age-policy columns are absent")
    results, scores = {}, {}
    for name in ("exclude_gender_keep_age", "include_gender", "exclude_gender_and_age"):
        removed = set(cfg.age_columns) if name == "exclude_gender_and_age" else set()
        cols = FeatureColumns(
            numeric=[c for c in numeric if c not in removed],
            categorical=categorical + ([cfg.gender_column] if name == "include_gender" else []),
            log=[c for c in spec.log if c not in removed],
        )
        pipe = fit_pipeline(
            make_lgbm_pipeline(lgbm, columns=cols, config_dir=str(config_dir)), X_tr[cols.all], y_tr
        )
        scores[name] = predict_proba(pipe, X_se[cols.all])
        results[name] = compute_metrics(y_se.to_numpy(), scores[name])
    baseline = scores[cfg.default_policy]
    comparisons = {
        name: paired_bootstrap(
            y_se.to_numpy(),
            values,
            baseline,
            n_resamples=cfg.bootstrap_resamples,
            seed=cfg.bootstrap_seed,
            ci_level=cfg.ci_level,
        )
        for name, values in scores.items()
        if name != cfg.default_policy
    }
    predictions = save_predictions(
        data_cfg.reports_dir / "experiments/policy/selection_predictions.parquet",
        X_se.index,
        y_se.to_numpy(),
        scores,
    )
    summary = {
        "policy": cfg.default_policy,
        "decision": "Keep gender excluded and age included; comparisons are descriptive only.",
        "family": "fixed_lightgbm",
        "selection_metrics": results,
        "comparisons_vs_default": comparisons,
        "predictions_path": str(predictions),
        "feature_groups": groups,
        "contract_version": registry.contract_version,
        "partition_hash": partition_info("train", config_dir=config_dir).partition_hash,
        "resolved_config": {"analysis": cfg.model_dump(), "model": lgbm.model_dump()},
        "protocol_hashes": protocol_hashes(root),
        "data_manifest_hash": sha256_file(data_cfg.manifest_path),
    }
    summary["run_id"] = _log_experiment(
        cfg.experiment_name,
        root,
        summary,
        {f"{name}_roc_auc": values["roc_auc"] for name, values in results.items()},
    )
    analysis_manifest("policy", summary, data_cfg=data_cfg, root=root)
    return summary
