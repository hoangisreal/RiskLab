"""E5: post-lock challenger study on the served feature set.

XGBoost, CatBoost and HistGradientBoosting are fitted on Train and scored on
Selection alongside the frozen champion protocol (LightGBM refit with its
E1-served inner-CV outcome, Logistic Regression refit at its chosen C). The
study is descriptive (docs/DECISIONS.md D21): it never changes the served
artifact, and its config lives in ``experiments/e5.yaml`` outside the
protocol-hashed ``configs/`` tree so it cannot invalidate the selection lock.
The Test partition is never read.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import yaml
from catboost import CatBoostClassifier  # type: ignore[import-untyped]
from pydantic import Field
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from risklab.config import (
    DataConfig,
    PartitionConfig,
    _Strict,
    load_data_config,
    load_partition_config,
)
from risklab.data.loaders import PartitionRole, partition_info
from risklab.experiments.artifacts import analysis_manifest
from risklab.features.application import FeatureColumns, feature_spec, load_features_config
from risklab.features.historical import combined_columns, load_full_frame
from risklab.features.registry import load_registry
from risklab.manifest import library_versions, sha256_file, write_json_atomic
from risklab.models.evaluate import (
    METRIC_NAMES,
    compute_metrics,
    paired_bootstrap,
    save_predictions,
)
from risklab.models.pipelines import (
    load_lgbm_params,
    load_lr_params,
    make_lgbm_pipeline,
    make_lr_pipeline,
    output_feature_names,
)
from risklab.models.select import protocol_hashes
from risklab.models.train import fit_pipeline
from risklab.tracking import provenance_tags, tracked_run

CHAMPION = "lightgbm"
CHALLENGERS = ("xgboost", "catboost", "hist_gradient_boosting", "logistic_regression")


class E5Error(Exception):
    """Raised for invalid E5 inputs or protocol violations."""


class _Decision(_Strict):
    primary_metric: str = "roc_auc"
    min_gain: float = Field(ge=0)
    bootstrap_resamples: int = Field(ge=1)
    bootstrap_seed: int
    ci_level: float = Field(gt=0, lt=1)


class _Champion(_Strict):
    lgbm_n_estimators: int = Field(ge=1)
    lr_c: float = Field(gt=0)


class _XGBoost(_Strict):
    n_estimators: int = Field(ge=1)
    learning_rate: float = Field(gt=0)
    max_depth: int = Field(ge=1)
    subsample: float = Field(gt=0, le=1)
    colsample_bytree: float = Field(gt=0, le=1)
    reg_lambda: float = Field(ge=0)


class _CatBoost(_Strict):
    iterations: int = Field(ge=1)
    learning_rate: float = Field(gt=0)
    depth: int = Field(ge=1)


class _HistGB(_Strict):
    max_iter: int = Field(ge=1)
    learning_rate: float = Field(gt=0)
    max_leaf_nodes: int = Field(ge=2)
    min_samples_leaf: int = Field(ge=1)
    l2_regularization: float = Field(ge=0)


class E5Config(_Strict):
    experiment_name: str
    seed: int
    champion: _Champion
    xgboost: _XGBoost
    catboost: _CatBoost
    hist_gradient_boosting: _HistGB
    decision: _Decision


def load_e5_config(path: Path | str) -> E5Config:
    """Load and validate the E5 config declared in ``experiments/e5.yaml``."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return E5Config.model_validate(raw)


def _onehot_pipeline(base_lr: Pipeline, estimator: object) -> Pipeline:
    """Unfitted pipeline: the LR one-hot preprocessing with another estimator."""
    return Pipeline([("prep", clone(base_lr.named_steps["prep"])), ("model", estimator)])


def build_pipelines(
    cfg: E5Config,
    *,
    config_dir: Path | str | None,
    columns: FeatureColumns,
) -> dict[str, Pipeline]:
    """Unfitted pipelines for the champion protocol and every challenger."""
    cfg_dir_str = None if config_dir is None else str(config_dir)
    features_cfg = load_features_config(config_dir)
    lr_params = load_lr_params(config_dir).model_copy(update={"C": cfg.champion.lr_c})
    lgbm_params = load_lgbm_params(config_dir).model_copy(
        update={"n_estimators": cfg.champion.lgbm_n_estimators}
    )
    lr = make_lr_pipeline(lr_params, features_cfg, columns=columns, config_dir=cfg_dir_str)
    seed = cfg.seed
    return {
        CHAMPION: make_lgbm_pipeline(
            lgbm_params, features_cfg, columns=columns, config_dir=cfg_dir_str
        ),
        "xgboost": _onehot_pipeline(
            lr,
            XGBClassifier(
                n_estimators=cfg.xgboost.n_estimators,
                learning_rate=cfg.xgboost.learning_rate,
                max_depth=cfg.xgboost.max_depth,
                subsample=cfg.xgboost.subsample,
                colsample_bytree=cfg.xgboost.colsample_bytree,
                reg_lambda=cfg.xgboost.reg_lambda,
                tree_method="hist",
                eval_metric="logloss",
                random_state=seed,
                n_jobs=4,
            ),
        ),
        "catboost": _onehot_pipeline(
            lr,
            CatBoostClassifier(
                iterations=cfg.catboost.iterations,
                learning_rate=cfg.catboost.learning_rate,
                depth=cfg.catboost.depth,
                loss_function="Logloss",
                random_seed=seed,
                verbose=0,
            ),
        ),
        "hist_gradient_boosting": _onehot_pipeline(
            lr,
            HistGradientBoostingClassifier(
                max_iter=cfg.hist_gradient_boosting.max_iter,
                learning_rate=cfg.hist_gradient_boosting.learning_rate,
                max_leaf_nodes=cfg.hist_gradient_boosting.max_leaf_nodes,
                min_samples_leaf=cfg.hist_gradient_boosting.min_samples_leaf,
                l2_regularization=cfg.hist_gradient_boosting.l2_regularization,
                random_state=seed,
            ),
        ),
        "logistic_regression": lr,
    }


def run_e5(
    *,
    config_path: Path,
    config_dir: Path | str | None,
    root: Path,
    output_dir: Path,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    tracking: bool = True,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Run E5 end to end and return the summary (also written to ``output_dir``)."""
    cfg = load_e5_config(config_path)
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    features_cfg = load_features_config(config_dir)

    e2_path = data_cfg.reports_dir / "experiments/e2/summary.json"
    if not e2_path.is_file():
        raise E5Error("run E2 for the current feature contract before E5")
    served = [g for g in json.loads(e2_path.read_text())["served_groups"] if g != "application"]

    info = partition_info(
        PartitionRole.train,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=None if config_dir is None else Path(config_dir),
    )
    frame_kw: dict[str, Any] = dict(
        data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=config_dir, root=root
    )
    X_tr, y_tr = load_full_frame("train", "fit", served, **frame_kw)
    X_se, y_se = load_full_frame("selection", "select", served, **frame_kw)
    numeric, categorical = combined_columns(served, config_dir=config_dir)
    base_columns = feature_spec(features_cfg, config_dir=config_dir)
    columns = FeatureColumns(numeric=numeric, categorical=categorical, log=base_columns.log)
    X_tr, X_se = X_tr[columns.all], X_se[columns.all]
    log(f"Train {X_tr.shape}, Selection {X_se.shape}")
    if list(X_tr.columns) != list(X_se.columns):
        raise E5Error("Train and Selection frames have different columns")

    pipes = build_pipelines(cfg, config_dir=config_dir, columns=columns)
    fit_seconds: dict[str, float] = {}
    fitted: dict[str, Pipeline] = {}
    for name, pipe in pipes.items():
        t0 = time.monotonic()
        fitted[name] = fit_pipeline(pipe, X_tr, y_tr)
        fit_seconds[name] = round(time.monotonic() - t0, 1)
        log(f"{name}: final fit on Train in {fit_seconds[name]}s")

    y_sel = y_se.to_numpy()
    scores = {
        name: np.asarray(p.predict_proba(X_se), dtype=float)[:, 1] for name, p in fitted.items()
    }
    metrics = {name: compute_metrics(y_sel, s) for name, s in scores.items()}
    comparisons: dict[str, Any] = {}
    for name in CHALLENGERS:
        boot = paired_bootstrap(
            y_sel,
            scores[name],
            scores[CHAMPION],
            n_resamples=cfg.decision.bootstrap_resamples,
            seed=cfg.decision.bootstrap_seed,
            ci_level=cfg.decision.ci_level,
        )
        primary = boot[cfg.decision.primary_metric]
        comparisons[name] = {
            "paired_bootstrap_vs_champion": boot,
            "flagged_superior": bool(
                primary["delta"] >= cfg.decision.min_gain and primary["ci_low"] > 0
            ),
        }
        log(
            f"{name} vs champion: delta ROC-AUC {primary['delta']:+.6f} "
            f"CI [{primary['ci_low']:+.6f}, {primary['ci_high']:+.6f}] "
            f"flagged={comparisons[name]['flagged_superior']}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths: dict[str, str] = {}
    artifact_hashes: dict[str, str] = {}
    for name, pipe in fitted.items():
        payload = pickle.dumps(pipe)
        digest = hashlib.sha256(payload).hexdigest()
        artifact = output_dir / f"{name}-{digest}.pkl"
        if not artifact.is_file():
            artifact.write_bytes(payload)
        artifact_paths[name], artifact_hashes[name] = str(artifact), digest
    pred_path = save_predictions(
        output_dir / "selection_predictions.parquet", X_se.index.to_numpy(), y_sel, scores
    )

    resolved: dict[str, Any] = {"e5": cfg.model_dump(mode="json")}
    summary: dict[str, Any] = {
        "experiment": cfg.experiment_name,
        "study": "post-lock challenger study (descriptive, D21); served artifact unchanged",
        "feature_groups": served,
        "contract_version": load_registry(config_dir).contract_version,
        "library_versions": library_versions(),
        "protocol_hashes": protocol_hashes(root),
        "data_manifest_hash": sha256_file(data_cfg.manifest_path),
        "model_paths": artifact_paths,
        "model_sha256": artifact_hashes,
        "predictions_path": str(pred_path),
        "partition_hash": info.partition_hash,
        "train_rows": int(len(X_tr)),
        "selection_rows": int(len(X_se)),
        "n_input_features": int(X_tr.shape[1]),
        "n_output_features": {n: len(output_feature_names(p)) for n, p in fitted.items()},
        "fit_seconds": fit_seconds,
        "selection_metrics": metrics,
        "comparisons_vs_champion": comparisons,
        "run_ids": {},
    }

    if tracking:
        tags = provenance_tags(
            root=root,
            partition_hash=info.partition_hash,
            config=resolved,
            extra={"experiment_id": "E5", "fit_role": "train", "eval_role": "selection"},
        )
        for name in fitted:
            with tracked_run(
                cfg.experiment_name, name, {**tags, "model": name}, {"model": name}
            ) as run_id:
                summary["run_ids"][name] = run_id
                mlflow.log_metrics({f"selection_{k}": v for k, v in metrics[name].items()})
                mlflow.log_metrics(
                    {
                        "train_rows": len(X_tr),
                        "selection_rows": len(X_se),
                        "fit_seconds": fit_seconds[name],
                    }
                )
                mlflow.log_artifact(artifact_paths[name])
        with tracked_run(
            cfg.experiment_name, "comparison", {**tags, "model": "comparison"}, {"study": "e5"}
        ) as run_id:
            summary["run_ids"]["comparison"] = run_id
            for name in CHALLENGERS:
                boot = comparisons[name]["paired_bootstrap_vs_champion"]
                for m in METRIC_NAMES:
                    mlflow.log_metrics(
                        {
                            f"{name}_delta_{m}": boot[m]["delta"],
                            f"{name}_delta_{m}_ci_low": boot[m]["ci_low"],
                            f"{name}_delta_{m}_ci_high": boot[m]["ci_high"],
                        }
                    )
            mlflow.log_dict(summary | {"resolved_config": resolved}, "summary.json")

    write_json_atomic(output_dir / "summary.json", summary | {"resolved_config": resolved})
    analysis_manifest(
        "train_served", summary | {"resolved_config": resolved}, data_cfg=data_cfg, root=root
    )
    return summary


__all__ = ["CHALLENGERS", "CHAMPION", "E5Config", "E5Error", "load_e5_config", "run_e5"]
