"""Bounded Train-CV CUDA challenger and descriptive served-set robustness study.

No Test reads, calibration choices, threshold tuning, locks or registry mutations.
The champion protocol is unchanged; a descriptive win cannot promote a model.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import pickle
import time
import warnings
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import yaml
from sklearn.base import clone
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from risklab.features.application import FeatureColumns
from risklab.features.historical import combined_columns, load_full_frame
from risklab.features.registry import load_registry
from risklab.manifest import git_info, library_versions, sha256_file, write_json_atomic
from risklab.models.evaluate import compute_metrics, paired_bootstrap
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.select import protocol_hashes
from risklab.tracking import require_tracking_uri
from risklab.verify import verify_reference


def subset_columns(columns: FeatureColumns, removed: set[str]) -> FeatureColumns:
    return FeatureColumns(
        numeric=[c for c in columns.numeric if c not in removed],
        categorical=[c for c in columns.categorical if c not in removed],
        log=[c for c in columns.log if c not in removed],
    )


def tune_cuda(
    X: pd.DataFrame, y: pd.Series, prep: Any, cfg: dict[str, Any]
) -> tuple[XGBClassifier, list[dict[str, Any]]]:
    """Fit preprocessing independently in each Train fold; never accepts Selection."""
    folds = list(
        StratifiedKFold(n_splits=cfg["folds"], shuffle=True, random_state=cfg["seed"]).split(X, y)
    )
    candidates = []
    for depth in cfg["depths"]:
        for weight in cfg["child_weights"]:
            scores, rounds = [], []
            for tr, va in folds:
                fold_prep = clone(prep)
                a = fold_prep.fit_transform(X.iloc[tr], y.iloc[tr])
                b = fold_prep.transform(X.iloc[va])
                model = XGBClassifier(
                    device="cuda:0",
                    tree_method="hist",
                    enable_categorical=True,
                    n_estimators=cfg["max_rounds"],
                    max_depth=depth,
                    min_child_weight=weight,
                    learning_rate=cfg["learning_rate"],
                    subsample=0.8,
                    colsample_bytree=0.8,
                    reg_lambda=1.0,
                    random_state=cfg["seed"],
                    n_jobs=cfg["threads"],
                    early_stopping_rounds=cfg["early_stopping_rounds"],
                    eval_metric="auc",
                )
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    model.fit(a, y.iloc[tr], eval_set=[(b, y.iloc[va])], verbose=False)
                effective = json.loads(model.get_booster().save_config())
                if effective["learner"]["generic_param"]["device"] != "cuda:0":
                    raise RuntimeError("CUDA fit fell back to CPU")
                if any("no visible gpu" in str(w.message).lower() for w in caught):
                    raise RuntimeError("CUDA training unavailable")
                rounds.append(int(model.best_iteration) + 1)
                scores.append(float(model.best_score))
            candidates.append(
                {
                    "depth": depth,
                    "child_weight": weight,
                    "mean_auc": float(np.mean(scores)),
                    "fold_auc": scores,
                    "rounds": rounds,
                }
            )
    # Stable preference for the simpler candidate on exact ties.
    best = max(candidates, key=lambda c: (c["mean_auc"], -c["depth"], c["child_weight"]))
    return XGBClassifier(
        device="cuda:0",
        tree_method="hist",
        enable_categorical=True,
        n_estimators=max(1, round(float(np.mean(best["rounds"])))),
        max_depth=best["depth"],
        min_child_weight=best["child_weight"],
        learning_rate=cfg["learning_rate"],
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        random_state=cfg["seed"],
        n_jobs=cfg["threads"],
    ), candidates


def run_study(config: Path, baseline: Path, output: Path) -> dict[str, Any]:
    require_tracking_uri()
    if output.exists():
        raise ValueError("study output must not exist")
    root = Path(__file__).resolve().parents[3]
    verified = verify_reference(root / "reports/reference/contract-1.1", baseline / "reports")
    if not verified["passed"]:
        raise ValueError("a reference-verified baseline is required")
    cfg = yaml.safe_load(config.read_text())
    config_dir = baseline / "configs"
    numeric, categorical = combined_columns(cfg["groups"], config_dir=config_dir)
    columns = FeatureColumns(numeric=numeric, categorical=categorical, log=[])
    if {"TARGET", "SK_ID_CURR", "CODE_GENDER"} & set(columns.all):
        raise ValueError("reserved input in model columns")
    train, y_train = load_full_frame("train", "fit", cfg["groups"], config_dir=config_dir)
    selection, y_selection = load_full_frame(
        "selection", "select", cfg["groups"], config_dir=config_dir
    )
    train, selection = train[columns.all], selection[columns.all]
    params = load_lgbm_params(config_dir).model_copy(
        update={"n_estimators": cfg["champion_rounds"], "n_jobs": cfg["threads"]}
    )
    champion = make_lgbm_pipeline(params, columns=columns, config_dir=config_dir)
    output.mkdir(parents=True)
    verification = write_json_atomic(output / "baseline-verification.json", verified)
    mlflow.set_experiment("risklab-e6-portfolio-robustness")
    with mlflow.start_run() as run:
        mlflow.log_artifact(str(config), "protocol")
        started = time.monotonic()
        champion.fit(train, y_train)
        reference = champion.predict_proba(selection)[:, 1]
        rows: dict[str, Any] = {"champion": {"metrics": compute_metrics(y_selection, reference)}}
        registry = load_registry(config_dir)
        external = {c for c in columns.all if c.startswith("EXT_SOURCE_")}
        removals = {"without_all_external": external}
        removals.update({f"without_{c.lower()}": {c} for c in sorted(external)})
        removals.update({f"without_{g}": set(registry.group_features([g])) for g in cfg["groups"]})
        for name, removed in removals.items():
            cols = subset_columns(columns, removed)
            pipe = make_lgbm_pipeline(params, columns=cols, config_dir=config_dir)
            pipe.fit(train[cols.all], y_train)
            scores = pipe.predict_proba(selection[cols.all])[:, 1]
            rows[name] = {
                "removed_columns": sorted(removed),
                "metrics": compute_metrics(y_selection, scores),
                "paired_auc": paired_bootstrap(
                    y_selection,
                    scores,
                    reference,
                    n_resamples=cfg["bootstrap_resamples"],
                    seed=cfg["seed"],
                    metrics=["roc_auc"],
                )["roc_auc"],
            }
            print(f"Completed {name}", flush=True)
        model, cv = tune_cuda(train, y_train, champion.named_steps["prep"], cfg)
        prep = clone(champion.named_steps["prep"])
        prepared_train = prep.fit_transform(train, y_train)
        prepared_selection = prep.transform(selection)
        model.fit(prepared_train, y_train)
        effective = json.loads(model.get_booster().save_config())
        if effective["learner"]["generic_param"]["device"] != "cuda:0":
            raise RuntimeError("final fit did not use CUDA")
        model.set_params(device="cpu")
        scores = model.predict_proba(prepared_selection)[:, 1]
        artifact = output / "research-xgboost.pkl"
        artifact.write_bytes(pickle.dumps(Pipeline([("prep", prep), ("model", model)])))
        # Only self-created, trusted bytes are deserialized; never load downloaded pickles.
        restored = pickle.loads(artifact.read_bytes())
        restored_scores = restored.predict_proba(selection)[:, 1]
        if not np.array_equal(scores, restored_scores):
            raise RuntimeError("CPU artifact reload changed research predictions")
        paired = paired_bootstrap(
            y_selection,
            scores,
            reference,
            n_resamples=cfg["bootstrap_resamples"],
            seed=cfg["seed"],
            metrics=["roc_auc"],
        )["roc_auc"]
        rows["xgboost_cuda"] = {
            "metrics": compute_metrics(y_selection, scores),
            "paired_auc": paired,
            "train_cv": cv,
            "flagged_superior": paired["delta"] >= cfg["min_gain"] and paired["ci_low"] > 0,
            "chosen_params": model.get_params(),
            "training_device": effective["learner"]["generic_param"]["device"],
            "prediction_device": "cpu",
            "artifact_sha256": sha256_file(artifact),
            "cpu_reload_max_probability_difference": 0.0,
        }
        stress = selection.copy()
        stress[list(external)] = np.nan
        rows["external_unavailable_simulation"] = {
            "metrics": compute_metrics(y_selection, champion.predict_proba(stress)[:, 1]),
            "semantics": "feature-level fault simulation; source validation remains required",
        }
        threshold = cfg["threshold"]
        policy = []
        for t in [0.05, threshold, 0.10, 0.15, 0.20]:
            flagged = reference >= t
            policy.append(
                {
                    "threshold": t,
                    "review_fraction": float(flagged.mean()),
                    "recall": float(flagged[y_selection.to_numpy() == 1].mean()),
                }
            )
        report = {
            "run_id": run.info.run_id,
            "study": "E6 descriptive; no promotion or Test evaluation",
            "config_sha256": sha256_file(config),
            "source_sha256": sha256_file(Path(__file__)),
            "baseline_verification_sha256": sha256_file(verification),
            "data_manifest_sha256": sha256_file(baseline / "data/manifest.json"),
            "partition_hash": json.loads(
                (baseline / "data/partitions/partitions.json").read_text()
            )["partition_hash"],
            "runtime": {**library_versions(), "xgboost": importlib.metadata.version("xgboost")},
            "git": git_info(root),
            "protocol_hashes": protocol_hashes(root),
            "effective_isolated_protocol_hashes": protocol_hashes(baseline),
            "config": cfg,
            "train_rows": len(train),
            "selection_rows": len(selection),
            "results": rows,
            "fixed_threshold_scenarios": policy,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "limitations": [
                "Selection reused; intervals exploratory without multiplicity correction",
                "GPU arithmetic can vary; no exact CPU/GPU artifact equality claimed",
                "Threshold scenarios are descriptive; frozen policy unchanged",
                "Research challenger is not integrated into the application's explanation contract",
            ],
        }
        write_json_atomic(output / "summary.json", report)
        for name, result in rows.items():
            mlflow.log_metrics({f"{name}.{k}": v for k, v in result["metrics"].items()})
        mlflow.log_artifact(str(output / "summary.json"))
    print(f"E6 complete; run {report['run_id']}; no Test evaluation or registry change", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("experiments/e6.yaml"))
    args = parser.parse_args()
    run_study(args.config.resolve(), args.baseline.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()
