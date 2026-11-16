"""Fixed-budget model breadth study on Train/Calibration/Selection, never Test."""

from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import pickle
import time
import warnings
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import yaml
from sklearn.base import clone
from sklearn.ensemble import AdaBoostClassifier, ExtraTreesClassifier, RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.naive_bayes import GaussianNB
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier

from risklab.experiments.e5 import build_pipelines as baseline_pipelines
from risklab.experiments.e5 import load_e5_config
from risklab.features.application import FeatureColumns, feature_spec, load_features_config
from risklab.features.historical import combined_columns, load_full_frame
from risklab.manifest import git_info, library_versions, sha256_file, write_json_atomic
from risklab.models.calibrate import expected_calibration_error, fit_calibrator
from risklab.models.evaluate import compute_metrics, paired_bootstrap, save_predictions
from risklab.models.select import protocol_hashes
from risklab.tracking import require_tracking_uri
from risklab.verify import verify_reference


def build_pipelines(
    cfg: dict[str, Any], config_dir: Path, columns: FeatureColumns, root: Path
) -> dict[str, Pipeline]:
    """Keep the champion preprocessing; use the existing Train-fitted LR prep elsewhere."""
    pipes = baseline_pipelines(
        load_e5_config(root / cfg["shared_baselines"]), config_dir=config_dir, columns=columns
    )
    pipes["xgboost"].set_params(model__device="cuda:0")
    pipes["catboost"].set_params(
        model__task_type="GPU",
        model__devices="0",
        model__gpu_ram_part=0.6,
        model__thread_count=cfg["threads"],
        model__allow_writing_files=False,
    )
    prep = pipes["logistic_regression"].named_steps["prep"]
    seed = cfg["seed"]
    svm = {k: v for k, v in cfg["linear_svm"].items() if k != "calibration"}
    mlp = {**cfg["mlp"], "hidden_layer_sizes": tuple(cfg["mlp"]["hidden_layer_sizes"])}
    estimators = {
        "decision_tree": DecisionTreeClassifier(**cfg["decision_tree"], random_state=seed),
        "random_forest": RandomForestClassifier(
            **cfg["random_forest"], random_state=seed, n_jobs=cfg["threads"]
        ),
        "extra_trees": ExtraTreesClassifier(
            **cfg["extra_trees"], random_state=seed, n_jobs=cfg["threads"]
        ),
        "adaboost": AdaBoostClassifier(**cfg["adaboost"], random_state=seed),
        "gaussian_nb": GaussianNB(**cfg["gaussian_nb"]),
        "mlp": MLPClassifier(**mlp, random_state=seed),
        "linear_svm_sigmoid": LinearSVC(**svm, dual=False, random_state=seed),
    }
    pipes.update(
        {
            name: Pipeline([("prep", clone(prep)), ("model", model)])
            for name, model in estimators.items()
        }
    )
    return pipes


def prepare_prediction(pipe: Pipeline, name: str, threads: int) -> str:
    """Verify GPU fits and fix forest reduction order for exact reload checks."""
    if name in {"random_forest", "extra_trees"}:
        pipe.set_params(model__n_jobs=threads)
    if name == "xgboost":
        effective = json.loads(pipe.named_steps["model"].get_booster().save_config())
        device = effective["learner"]["generic_param"]["device"]
        if device != "cuda:0":
            raise RuntimeError("XGBoost fit fell back from CUDA")
        pipe.set_params(model__device="cpu")
        return str(device)
    if name == "catboost":
        if pipe.named_steps["model"].get_all_params()["task_type"] != "GPU":
            raise RuntimeError("CatBoost did not fit on GPU")
        return "cuda:0"
    return "cpu"


def run_study(config: Path, baseline: Path, output: Path) -> dict[str, Any]:
    require_tracking_uri()
    root = Path(__file__).resolve().parents[3]
    if output.exists():
        raise ValueError("output must not exist")
    verification = verify_reference(root / "reports/reference/contract-1.1", baseline / "reports")
    if not verification["passed"]:
        raise ValueError("a verified baseline is required")
    cfg = yaml.safe_load(config.read_text())
    config_dir = baseline / "configs"
    frames = {
        role: load_full_frame(role, purpose, cfg["groups"], config_dir=config_dir)
        for role, purpose in [
            ("train", "fit"),
            ("calibration", "calibrate"),
            ("selection", "select"),
        ]
    }
    numeric, categorical = combined_columns(cfg["groups"], config_dir=config_dir)
    log = feature_spec(load_features_config(config_dir), config_dir=config_dir).log
    columns = FeatureColumns(numeric=numeric, categorical=categorical, log=log)
    if set(columns.all) & {"TARGET", "SK_ID_CURR", "CODE_GENDER"}:
        raise ValueError("reserved model input")
    train, y_train = frames["train"]
    calibration, y_cal = frames["calibration"]
    selection, y_sel = frames["selection"]
    train, calibration, selection = (f[columns.all] for f in (train, calibration, selection))
    pipes = build_pipelines(cfg, config_dir, columns, root)
    # Fit and score every family in a fixed order; never adapt settings to Selection.
    output.mkdir(parents=True)
    mlflow.set_experiment("risklab-e7-model-breadth")
    results: dict[str, Any] = {}
    scores: dict[str, np.ndarray] = {}
    with mlflow.start_run() as run:
        mlflow.log_artifact(str(config), "protocol")
        mlflow.log_artifact(str(root / cfg["shared_baselines"]), "protocol")
        for name in list(pipes):
            pipe = pipes.pop(name)
            start = time.monotonic()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                pipe.fit(train, y_train)
            fit_seconds = time.monotonic() - start
            calibration_start = time.monotonic()
            model: Any = pipe
            method = "none"
            if name == "linear_svm_sigmoid":
                method = cfg["linear_svm"]["calibration"]
                model = fit_calibrator(pipe, calibration, y_cal, method)
            calibration_seconds = time.monotonic() - calibration_start
            device = prepare_prediction(pipe, name, cfg["prediction_threads"])
            start = time.monotonic()
            p = np.asarray(model.predict_proba(selection)[:, 1], dtype=np.float64)
            score_seconds = time.monotonic() - start
            scores[name] = p
            artifact = output / f"{name}.pkl"
            artifact.write_bytes(pickle.dumps(model))
            restored = pickle.loads(artifact.read_bytes())
            if not np.array_equal(p[:32], restored.predict_proba(selection.iloc[:32])[:, 1]):
                raise RuntimeError(f"{name} reload changed probe probabilities")
            row: dict[str, Any] = {
                "metrics": compute_metrics(y_sel, p),
                "ece": expected_calibration_error(y_sel.to_numpy(), p),
                "fit_seconds": round(fit_seconds, 3),
                "calibration_seconds": round(calibration_seconds, 3),
                "selection_batch_score_seconds": round(score_seconds, 3),
                "training_device": device,
                "calibration": method,
                "preprocessing": "native categorical" if name == "lightgbm" else "LR one-hot",
                "artifact_sha256": sha256_file(artifact),
                "convergence_warning": any(
                    issubclass(w.category, ConvergenceWarning) for w in caught
                ),
                "warning_types": sorted({w.category.__name__ for w in caught}),
            }
            if name != "lightgbm":
                paired = paired_bootstrap(
                    y_sel,
                    p,
                    scores["lightgbm"],
                    n_resamples=cfg["bootstrap_resamples"],
                    seed=cfg["seed"],
                    metrics=["roc_auc"],
                )["roc_auc"]
                row["paired_auc"] = paired
                row["flagged_superior"] = (
                    paired["delta"] >= cfg["min_gain"] and paired["ci_low"] > 0
                )
            results[name] = row
            mlflow.log_metrics({f"{name}.{k}": v for k, v in row["metrics"].items()})
            write_json_atomic(
                output / "progress.json", {"run_id": run.info.run_id, "completed": list(results)}
            )
            print(f"Completed {name}: AUC {row['metrics']['roc_auc']:.6f}", flush=True)
            # Keep only small scores/metadata after persisting the fitted model.
            del model, restored, pipe
            gc.collect()
        save_predictions(
            output / "selection_predictions.parquet",
            selection.index.to_numpy(),
            y_sel.to_numpy(),
            scores,
        )
        report = {
            "run_id": run.info.run_id,
            "study": "E7 descriptive; no promotion or Test evaluation",
            "config_sha256": sha256_file(config),
            "source_sha256": sha256_file(Path(__file__)),
            "shared_baselines_sha256": sha256_file(root / cfg["shared_baselines"]),
            "source_protocol_hashes": protocol_hashes(root),
            "effective_protocol_hashes": protocol_hashes(baseline),
            "data_manifest_sha256": sha256_file(baseline / "data/manifest.json"),
            "git": git_info(root),
            "runtime": {
                **library_versions(),
                "xgboost": importlib.metadata.version("xgboost"),
                "catboost": importlib.metadata.version("catboost"),
            },
            "config": cfg,
            "rows": {k: len(v[0]) for k, v in frames.items()},
            "results": results,
            "limitations": [
                "Selection reused; intervals exploratory without multiplicity correction",
                "Fixed-budget applied pipelines, not equally tuned family optima",
                "SVM sigmoid fitted on Calibration only; other families uncalibrated",
                "MLP/HistGB internal early stopping uses Train only",
                "Batch timings are not HTTP latency or production SLO evidence",
            ],
        }
        write_json_atomic(output / "summary.json", report)
        mlflow.log_artifact(str(output / "summary.json"))
    print(f"E7 completed: {report['run_id']}; {len(results)} families; Test not read", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("experiments/e7.yaml"))
    args = parser.parse_args()
    run_study(args.config.resolve(), args.baseline.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()
