"""E1: Logistic Regression vs LightGBM on identical application features and partitions.

Protocol (MASTER_PLAN Section 6): hyperparameters and boosting rounds come from inner CV on Train;
both models are refitted on Train and scored on the Selection partition; the decision uses a paired
bootstrap on Selection and the rule declared in ``configs/experiments/e1.yaml`` before the run.
The Test partition is never read. Every number reported here comes from an MLflow run.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import matplotlib
import mlflow
import numpy as np
import pandas as pd
from pydantic import Field, field_validator
from sklearn.metrics import precision_recall_curve, roc_curve
from sklearn.pipeline import Pipeline

from risklab.config import (
    DataConfig,
    PartitionConfig,
    _Strict,
    load_data_config,
    load_model,
    load_partition_config,
)
from risklab.data.loaders import PartitionRole, Purpose, partition_info
from risklab.features.application import load_application_frame, load_features_config
from risklab.manifest import write_json_atomic
from risklab.models.evaluate import (
    LOWER_IS_BETTER,
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
from risklab.models.train import fit_pipeline, tune_lgbm_rounds, tune_lr
from risklab.tracking import provenance_tags, tracked_run

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

E1_CONFIG = "experiments/e1.yaml"
LR_NAME = "logistic_regression"
LGBM_NAME = "lightgbm"


class E1Error(Exception):
    """Raised for invalid E1 inputs or an unusable run."""


class InnerCV(_Strict):
    folds: int = Field(ge=2)
    seed: int


class LRGrid(_Strict):
    c_grid: list[float] = Field(min_length=1)

    @field_validator("c_grid")
    @classmethod
    def _valid(cls, v: list[float]) -> list[float]:
        if any(c <= 0 for c in v) or len(set(v)) != len(v):
            raise ValueError("c_grid must contain unique positive values")
        return v


class LGBMRounds(_Strict):
    max_rounds: int = Field(ge=1)
    early_stopping_rounds: int = Field(ge=1)


class Decision(_Strict):
    primary_metric: Literal["roc_auc", "pr_auc", "brier", "log_loss"]
    min_gain: float = Field(ge=0)
    bootstrap_resamples: int = Field(ge=100)
    bootstrap_seed: int
    ci_level: float = Field(gt=0, lt=1)


class E1Config(_Strict):
    experiment_name: str = Field(min_length=1)
    feature_set: str = Field(min_length=1)
    seed: int
    inner_cv: InnerCV
    lr: LRGrid
    lgbm: LGBMRounds
    decision: Decision


def load_e1_config(config_dir: Path | str | None = None) -> E1Config:
    return load_model(config_dir, E1_CONFIG, E1Config)


def decide(boot_primary: dict[str, float], decision: Decision) -> dict[str, Any]:
    """Apply the pre-declared rule to the paired bootstrap of LightGBM minus LR.

    ``boot_primary`` holds ``delta``, ``ci_low`` and ``ci_high`` of the primary metric. For
    lower-is-better metrics the gain is the negated difference and the interval is mirrored.
    """
    if decision.primary_metric in LOWER_IS_BETTER:
        gain, lower = -boot_primary["delta"], -boot_primary["ci_high"]
    else:
        gain, lower = boot_primary["delta"], boot_primary["ci_low"]
    lgbm_wins = gain >= decision.min_gain and lower > 0
    if lgbm_wins:
        reason = (
            f"{LGBM_NAME} gains {gain:.5f} in {decision.primary_metric} (>= min_gain "
            f"{decision.min_gain}) and the {decision.ci_level:.0%} interval excludes 0"
        )
    else:
        reason = (
            f"gain {gain:.5f} in {decision.primary_metric} with lower bound {lower:.5f} does not "
            f"satisfy gain >= {decision.min_gain} and lower bound > 0; prefer the simpler "
            f"{LR_NAME}"
        )
    return {
        "preferred": LGBM_NAME if lgbm_wins else LR_NAME,
        "gain": gain,
        "ci_lower_bound_of_gain": lower,
        "min_gain": decision.min_gain,
        "reason": reason,
    }


def _plot_curves(y: np.ndarray, scores: dict[str, np.ndarray], path: Path) -> Path:
    fig, (ax_roc, ax_pr) = plt.subplots(1, 2, figsize=(10, 4))
    for name, s in scores.items():
        fpr, tpr, _ = roc_curve(y, s)
        prec, rec, _ = precision_recall_curve(y, s)
        ax_roc.plot(fpr, tpr, label=name)
        ax_pr.plot(rec, prec, label=name)
    ax_roc.plot([0, 1], [0, 1], "k:", linewidth=1)
    ax_roc.set(xlabel="False positive rate", ylabel="True positive rate", title="ROC (Selection)")
    ax_pr.axhline(float(y.mean()), color="k", linestyle=":", linewidth=1)
    ax_pr.set(xlabel="Recall", ylabel="Precision", title="Precision-recall (Selection)")
    ax_roc.legend()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _score(pipe: Pipeline, X: pd.DataFrame) -> np.ndarray:
    out: np.ndarray = pipe.predict_proba(X)[:, 1]
    return out


def run_e1(
    *,
    config_dir: Path | str | None,
    root: Path,
    output_dir: Path,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    tracking: bool = True,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Run E1 end to end and return the summary (also written to ``output_dir/summary.json``)."""
    cfg = load_e1_config(config_dir)
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    features_cfg = load_features_config(config_dir)
    lr_base, lgbm_base = load_lr_params(config_dir), load_lgbm_params(config_dir)
    cfg_dir_str = None if config_dir is None else str(config_dir)

    info = partition_info(
        PartitionRole.train,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=None if config_dir is None else Path(config_dir),
    )
    X_tr, y_tr = load_application_frame(
        PartitionRole.train,
        Purpose.fit,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=config_dir,
    )
    X_se, y_se = load_application_frame(
        PartitionRole.selection,
        Purpose.select,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=config_dir,
    )
    log(f"Train {X_tr.shape}, Selection {X_se.shape}")
    if list(X_tr.columns) != list(X_se.columns):
        raise E1Error("Train and Selection frames have different columns")

    t0 = time.monotonic()
    lr_tuning = tune_lr(
        X_tr,
        y_tr,
        lr_base,
        features_cfg,
        c_grid=cfg.lr.c_grid,
        folds=cfg.inner_cv.folds,
        seed=cfg.inner_cv.seed,
        config_dir=cfg_dir_str,
    )
    log(
        f"LR inner CV: best C={lr_tuning.best_c} mean AUC={lr_tuning.mean_auc} "
        f"({time.monotonic() - t0:.0f}s)"
    )
    t0 = time.monotonic()
    lgbm_tuning = tune_lgbm_rounds(
        X_tr,
        y_tr,
        lgbm_base,
        features_cfg,
        folds=cfg.inner_cv.folds,
        seed=cfg.inner_cv.seed,
        max_rounds=cfg.lgbm.max_rounds,
        early_stopping_rounds=cfg.lgbm.early_stopping_rounds,
        config_dir=cfg_dir_str,
    )
    log(
        f"LightGBM inner CV: rounds={lgbm_tuning.chosen_rounds} "
        f"per fold={lgbm_tuning.best_iterations} ({time.monotonic() - t0:.0f}s)"
    )

    lr_params = lr_base.model_copy(update={"C": lr_tuning.best_c})
    lgbm_params = lgbm_base.model_copy(update={"n_estimators": lgbm_tuning.chosen_rounds})
    fit_seconds: dict[str, float] = {}
    pipes: dict[str, Pipeline] = {}
    for name, pipe in (
        (LR_NAME, make_lr_pipeline(lr_params, features_cfg, config_dir=cfg_dir_str)),
        (LGBM_NAME, make_lgbm_pipeline(lgbm_params, features_cfg, config_dir=cfg_dir_str)),
    ):
        t0 = time.monotonic()
        pipes[name] = fit_pipeline(pipe, X_tr, y_tr)
        fit_seconds[name] = round(time.monotonic() - t0, 1)
        log(f"{name}: final fit on Train in {fit_seconds[name]}s")

    y_sel = y_se.to_numpy()
    scores = {name: _score(p, X_se) for name, p in pipes.items()}
    metrics = {name: compute_metrics(y_sel, s) for name, s in scores.items()}
    boot = paired_bootstrap(
        y_sel,
        scores[LGBM_NAME],
        scores[LR_NAME],
        n_resamples=cfg.decision.bootstrap_resamples,
        seed=cfg.decision.bootstrap_seed,
        ci_level=cfg.decision.ci_level,
    )
    decision = decide(boot[cfg.decision.primary_metric], cfg.decision)
    log(decision["reason"])

    output_dir.mkdir(parents=True, exist_ok=True)
    pred_path = save_predictions(
        output_dir / "selection_predictions.parquet", X_se.index.to_numpy(), y_sel, scores
    )
    plot_path = _plot_curves(y_sel, scores, output_dir / "selection_curves.png")

    resolved: dict[str, Any] = {
        "e1": cfg.model_dump(mode="json"),
        "features": features_cfg.model_dump(mode="json"),
        "lr": lr_params.model_dump(mode="json"),
        "lgbm": lgbm_params.model_dump(mode="json"),
    }
    summary: dict[str, Any] = {
        "experiment": cfg.experiment_name,
        "feature_set": cfg.feature_set,
        "partition_hash": info.partition_hash,
        "train_rows": int(len(X_tr)),
        "selection_rows": int(len(X_se)),
        "n_input_features": int(X_tr.shape[1]),
        "inner_cv": {
            LR_NAME: {
                "c_grid": lr_tuning.c_grid,
                "mean_auc": lr_tuning.mean_auc,
                "std_auc": lr_tuning.std_auc,
                "best_c": lr_tuning.best_c,
            },
            LGBM_NAME: {
                "best_iterations": lgbm_tuning.best_iterations,
                "fold_auc": lgbm_tuning.fold_auc,
                "chosen_rounds": lgbm_tuning.chosen_rounds,
            },
        },
        "n_output_features": {name: len(output_feature_names(p)) for name, p in pipes.items()},
        "fit_seconds": fit_seconds,
        "selection_metrics": metrics,
        "paired_bootstrap_lgbm_minus_lr": boot,
        "decision": decision,
        "run_ids": {},
    }

    if tracking:
        tags = provenance_tags(
            root=root,
            partition_hash=info.partition_hash,
            config=resolved,
            extra={"experiment_id": "E1", "fit_role": "train", "eval_role": "selection"},
        )
        for name, key in ((LR_NAME, "lr"), (LGBM_NAME, "lgbm")):
            with tracked_run(
                cfg.experiment_name, name, {**tags, "model": name}, resolved[key]
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
                if name == LR_NAME:
                    for c, mean_auc, std_auc in zip(
                        lr_tuning.c_grid, lr_tuning.mean_auc, lr_tuning.std_auc, strict=True
                    ):
                        mlflow.log_metrics(
                            {f"inner_cv_auc_C_{c}": mean_auc, f"inner_cv_auc_std_C_{c}": std_auc}
                        )
                else:
                    mlflow.log_metrics(
                        {
                            "inner_cv_mean_auc": float(np.mean(lgbm_tuning.fold_auc)),
                            "inner_cv_chosen_rounds": lgbm_tuning.chosen_rounds,
                        }
                    )
                mlflow.log_artifact(str(pred_path))
                mlflow.log_artifact(str(plot_path))
        with tracked_run(
            cfg.experiment_name,
            "comparison",
            {**tags, "model": "comparison"},
            cfg.decision.model_dump(),
        ) as run_id:
            summary["run_ids"]["comparison"] = run_id
            for m in METRIC_NAMES:
                mlflow.log_metrics(
                    {
                        f"delta_{m}": boot[m]["delta"],
                        f"delta_{m}_ci_low": boot[m]["ci_low"],
                        f"delta_{m}_ci_high": boot[m]["ci_high"],
                    }
                )
            mlflow.set_tag("preferred_model", decision["preferred"])
            mlflow.log_dict(summary | {"resolved_config": resolved}, "summary.json")

    write_json_atomic(output_dir / "summary.json", summary | {"resolved_config": resolved})
    return summary


def read_summary(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise E1Error(f"Cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise E1Error(f"{path} is not a JSON object")
    return loaded


__all__ = [
    "E1Config",
    "E1Error",
    "decide",
    "load_e1_config",
    "read_summary",
    "run_e1",
]
