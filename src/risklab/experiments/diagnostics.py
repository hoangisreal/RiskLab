"""Runner for the leakage diagnostics (label permutation and single-feature screening)."""

from __future__ import annotations

import json
import pickle
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mlflow
import numpy as np

from risklab.config import (
    DataConfig,
    PartitionConfig,
    load_data_config,
    load_model,
    load_partition_config,
)
from risklab.data.loaders import PartitionRole, Purpose, partition_info
from risklab.experiments.artifacts import analysis_manifest
from risklab.features.application import (
    FeatureColumns,
    feature_columns,
    load_application_frame,
    load_features_config,
)
from risklab.features.historical import combined_columns, load_full_frame
from risklab.manifest import write_json_atomic
from risklab.models.diagnostics import (
    DiagnosticsConfig,
    label_permutation_test,
    screening_report,
    single_feature_screen,
)
from risklab.models.pipelines import (
    load_lgbm_params,
    load_lr_params,
    make_lgbm_pipeline,
    make_lr_pipeline,
    output_feature_names,
)
from risklab.tracking import provenance_tags, tracked_run

DIAGNOSTICS_CONFIG = "experiments/diagnostics.yaml"
EXPERIMENT = "risklab-diagnostics"


def load_diagnostics_config(config_dir: Path | str | None = None) -> DiagnosticsConfig:
    return load_model(config_dir, DIAGNOSTICS_CONFIG, DiagnosticsConfig)


def run_diagnostics(
    *,
    config_dir: Path | str | None,
    root: Path,
    output_dir: Path,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    tracking: bool = True,
    log: Callable[[str], None] = lambda _msg: None,
    served_groups: list[str] | None = None,
) -> dict[str, Any]:
    """Run both diagnostics on the application model inputs; write ``application.json``."""
    cfg = load_diagnostics_config(config_dir)
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    features_cfg = load_features_config(config_dir)
    cfg_dir_str = None if config_dir is None else str(config_dir)
    cfg_dir_path = None if config_dir is None else Path(config_dir)
    kw: dict[str, Any] = {
        "data_cfg": data_cfg,
        "partition_cfg": partition_cfg,
        "config_dir": config_dir,
    }

    info = partition_info(
        PartitionRole.train,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=cfg_dir_path,
    )
    columns = None
    if served_groups is None:
        X_tr, y_tr = load_application_frame(PartitionRole.train, Purpose.fit, **kw)
        X_se, y_se = load_application_frame(PartitionRole.selection, Purpose.select, **kw)
        _, categorical = feature_columns(features_cfg, config_dir=config_dir)
    else:
        X_tr, y_tr = load_full_frame("train", "fit", served_groups, root=root, **kw)
        X_se, y_se = load_full_frame("selection", "select", served_groups, root=root, **kw)
        numeric, categorical = combined_columns(served_groups, config_dir=config_dir)
        from risklab.features.application import feature_spec

        spec = feature_spec(features_cfg, config_dir=config_dir)
        columns = FeatureColumns(numeric=numeric, categorical=categorical, log=spec.log)
        X_tr, X_se = X_tr[columns.all], X_se[columns.all]

    screen = single_feature_screen(X_tr, y_tr, categorical)
    screening = screening_report(
        screen,
        threshold=cfg.screening_threshold,
        expected_prefixes=cfg.expected_strong_prefixes,
        top_n=cfg.top_n,
    )
    log(
        f"Screening: {len(screening['expected_strong'])} expected strong, "
        f"{len(screening['to_investigate'])} to investigate"
    )

    lr_params, lgbm_params = load_lr_params(config_dir), load_lgbm_params(config_dir)
    makers = {
        "logistic_regression": lambda: make_lr_pipeline(
            lr_params, features_cfg, config_dir=cfg_dir_str, columns=columns
        ),
        "lightgbm": lambda: make_lgbm_pipeline(
            lgbm_params, features_cfg, config_dir=cfg_dir_str, columns=columns
        ),
    }
    permutation: dict[str, Any] = {}
    for name, make in makers.items():
        t0 = time.monotonic()
        permutation[name] = label_permutation_test(
            make,
            X_tr,
            y_tr,
            X_se,
            y_se,
            repeats=cfg.permutation_repeats,
            seed=cfg.seed,
            band=cfg.permutation_band,
        )
        log(
            f"Permutation {name}: AUCs={[round(a, 4) for a in permutation[name]['aucs']]} "
            f"passed={permutation[name]['passed']} ({time.monotonic() - t0:.0f}s)"
        )

    # Power control: evaluation rows that are also training rows must be detected by a model able to
    # memorise noise. If this stays inside the band the permutation test cannot be trusted.
    n_control = min(len(X_tr) // 5, len(X_se))
    control = label_permutation_test(
        makers["lightgbm"],
        X_tr,
        y_tr,
        X_tr.iloc[:n_control],
        y_tr.iloc[:n_control],
        repeats=1,
        seed=cfg.seed,
        band=cfg.permutation_band,
    )
    control["detected_overlap"] = not control["passed"]
    log(
        f"Power control (overlap): AUC={control['aucs'][0]:.4f} "
        f"detected={control['detected_overlap']}"
    )

    report: dict[str, Any] = {
        "feature_groups": served_groups,
        "resolved_config": cfg.model_dump(),
        "partition_hash": info.partition_hash,
        "train_rows": int(len(X_tr)),
        "selection_rows": int(len(X_se)),
        "label_permutation": permutation,
        "permutation_power_control": control,
        "single_feature_screening": screening,
        "passed": bool(
            screening["passed"]
            and all(p["passed"] for p in permutation.values())
            and control["detected_overlap"]
        ),
        "run_id": None,
    }
    e1_name = "e1_served" if served_groups is not None else "e1"
    e1_path = data_cfg.reports_dir / "experiments" / e1_name / "summary.json"
    if e1_path.is_file():
        e1 = json.loads(e1_path.read_text())
        winner = e1["decision"]["preferred"]
        with open(e1["model_paths"][winner], "rb") as fh:
            pipe = pickle.load(fh)
        estimator = pipe.named_steps["model"]
        if hasattr(estimator, "booster_"):
            values = estimator.booster_.feature_importance(importance_type="gain").astype(float)
            definition = "LightGBM split gain"
        else:
            values = np.abs(estimator.coef_[0])
            definition = "absolute LR coefficient on transformed features"
        names = output_feature_names(pipe)
        order = np.argsort(-values)
        shares = values / max(float(values.sum()), np.finfo(float).eps)
        cv = e1["inner_cv"][winner]
        mean_cv = (
            float(np.mean(cv["fold_auc"]))
            if winner == "lightgbm"
            else cv["mean_auc"][cv["c_grid"].index(cv["best_c"])]
        )
        gap = mean_cv - e1["selection_metrics"][winner]["roc_auc"]
        report["importance"] = {
            "definition": definition,
            "model_run_id": e1["run_ids"][winner],
            "top": [
                {"feature": str(names[i]), "share": float(shares[i])} for i in order[: cfg.top_n]
            ],
            "flagged": bool(shares.max() > cfg.maximum_importance_share),
        }
        report["train_cv_selection_gap"] = {
            "difference": gap,
            "flagged": abs(gap) > cfg.train_selection_auc_gap,
            "model_run_id": e1["run_ids"][winner],
        }
        report["passed"] = bool(
            report["passed"]
            and not report["importance"]["flagged"]
            and not report["train_cv_selection_gap"]["flagged"]
        )
    if tracking:
        resolved = {
            "diagnostics": cfg.model_dump(mode="json"),
            "features": features_cfg.model_dump(mode="json"),
        }
        tags = provenance_tags(
            root=root,
            partition_hash=info.partition_hash,
            config=resolved,
            extra={"experiment_id": "diagnostics", "fit_role": "train", "eval_role": "selection"},
        )
        with tracked_run(
            EXPERIMENT,
            "served" if served_groups is not None else "application",
            tags,
            cfg.model_dump(),
        ) as run_id:
            report["run_id"] = run_id
            for name, p in permutation.items():
                mlflow.log_metrics(
                    {
                        f"permutation_mean_auc_{name}": p["mean_auc"],
                        f"permutation_max_abs_dev_{name}": p["max_abs_deviation"],
                    }
                )
            mlflow.log_metric("screening_to_investigate", len(screening["to_investigate"]))
            mlflow.set_tag("passed", str(report["passed"]))
            mlflow.log_dict(report, "diagnostics.json")
    name = "served" if served_groups is not None else "application"
    write_json_atomic(output_dir / f"{name}.json", report)
    analysis_manifest(f"diagnostics_{name}", report, data_cfg=data_cfg, root=root)
    return report


__all__ = ["load_diagnostics_config", "run_diagnostics"]
