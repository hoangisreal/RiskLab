"""Retrospective chronological development benchmark on Home Credit Stability.

Only metadata are read for reserved periods. No final Test/Monitoring labels,
no model selection/promotion, and no claim that the unknown label horizon is purged.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import pickle
from pathlib import Path
from typing import Any

import duckdb
import mlflow
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml
from lightgbm import LGBMClassifier
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from risklab.manifest import library_versions, sha256_file, write_json_atomic
from risklab.models.evaluate import _BootstrapScorer, compute_metrics
from risklab.models.pipelines import CategoryCaster
from risklab.tracking import require_tracking_uri

ROLES = ("train", "calibration", "selection", "test", "monitoring")


def cpu_prediction(pipe: Pipeline, name: str) -> None:
    if name == "xgboost_cuda":
        effective = json.loads(pipe.named_steps["model"].get_booster().save_config())
        if effective["learner"]["generic_param"]["device"] != "cuda:0":
            raise RuntimeError("CUDA fit fell back to CPU")
        pipe.set_params(model__device="cpu")


def weekly_metrics(y: np.ndarray, p: np.ndarray, weeks: np.ndarray) -> list[dict[str, Any]]:
    result = []
    for week in sorted(np.unique(weeks)):
        mask = weeks == week
        result.append(
            {
                "week": int(week),
                "rows": int(mask.sum()),
                "positives": int(y[mask].sum()),
                "metrics": compute_metrics(y[mask], p[mask])
                if len(np.unique(y[mask])) == 2
                else None,
            }
        )
    return result


def temporal_roles(weeks: list[int], shares: dict[str, float], gap: int) -> dict[str, list[int]]:
    ordered = sorted(set(weeks))
    if not ordered or gap < 0 or abs(sum(shares.values()) - 1) > 1e-9:
        raise ValueError("invalid temporal protocol")
    result: dict[str, list[int]] = {}
    start, cumulative = 0, 0.0
    for role in ROLES:
        cumulative += shares[role]
        stop = len(ordered) if role == ROLES[-1] else int(len(ordered) * cumulative)
        chosen = ordered[start:stop]
        if start:
            chosen = [w for w in chosen if w > ordered[start - 1] + gap]
        if not chosen:
            raise ValueError("insufficient weeks for the declared gap/shares")
        result[role] = chosen
        start = stop
    return result


def load_development(raw: Path, role: str, weeks: list[int], cfg: dict[str, Any]) -> pd.DataFrame:
    if role not in {"train", "selection"}:
        raise ValueError("only Train/Selection labels are permitted in this study")
    features = cfg["numeric"] + cfg["categorical"]
    if set(features) & {"target", "case_id", "date_decision", "WEEK_NUM", "MONTH"}:
        raise ValueError("metadata/label cannot be a predictor")
    if any(not name.replace("_", "").isalnum() for name in features):
        raise ValueError("invalid feature name")
    fields = ", ".join(f's."{c}"' for c in features)
    con = duckdb.connect()
    try:
        con.execute("SET memory_limit='2GB'")
        con.execute(f"SET threads={int(cfg['threads'])}")
        # Filtering on week happens before labels leave the query. Reserved periods
        # are never returned, and sample choice does not reference the target.
        limit = f"LIMIT {int(cfg['train_cap'])}" if role == "train" else ""
        frame = con.execute(
            f"SELECT b.case_id,b.WEEK_NUM,b.target,{fields} FROM read_parquet(?) b "
            "JOIN read_parquet(?) s USING(case_id) WHERE b.WEEK_NUM BETWEEN ? AND ? "
            f"ORDER BY hash(b.case_id, {int(cfg['seed'])}) {limit}",
            [
                str(raw / "train_base.parquet"),
                [str(raw / f"train_static_0_{i}.parquet") for i in range(2)],
                min(weeks),
                max(weeks),
            ],
        ).df()
    finally:
        con.close()
    if frame.case_id.duplicated().any() or not set(frame.WEEK_NUM).issubset(weeks):
        raise ValueError("invalid join or temporal membership")
    for name in cfg["numeric"]:
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype("float32")
        frame[name] = frame[name].replace([np.inf, -np.inf], np.nan)
    for name in cfg["categorical"]:
        frame[name] = frame[name].fillna("__missing__").astype(str)
    return frame


def pipelines(cfg: dict[str, Any]) -> dict[str, Pipeline]:
    native = ColumnTransformer(
        [
            ("num", "passthrough", cfg["numeric"]),
            ("cat", CategoryCaster(min_count=100), cfg["categorical"]),
        ],
        verbose_feature_names_out=False,
    ).set_output(transform="pandas")
    encoded = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        (
                            "impute",
                            SimpleImputer(
                                strategy="median", add_indicator=True, keep_empty_features=True
                            ),
                        ),
                        ("scale", StandardScaler()),
                    ]
                ),
                cfg["numeric"],
            ),
            (
                "cat",
                OneHotEncoder(
                    handle_unknown="infrequent_if_exist",
                    min_frequency=0.005,
                    sparse_output=False,
                    dtype=np.float32,
                ),
                cfg["categorical"],
            ),
        ]
    )
    return {
        "logistic_regression": Pipeline(
            [
                ("prep", clone(encoded)),
                (
                    "model",
                    LogisticRegression(C=cfg["lr_c"], max_iter=500, random_state=cfg["seed"]),
                ),
            ]
        ),
        "lightgbm": Pipeline(
            [
                ("prep", clone(native)),
                (
                    "model",
                    LGBMClassifier(
                        n_estimators=cfg["lgbm_rounds"],
                        learning_rate=0.05,
                        num_leaves=31,
                        min_child_samples=100,
                        reg_lambda=1.0,
                        n_jobs=cfg["threads"],
                        random_state=cfg["seed"],
                        deterministic=True,
                        force_row_wise=True,
                        verbose=-1,
                    ),
                ),
            ]
        ),
        "xgboost_cuda": Pipeline(
            [
                ("prep", clone(native)),
                (
                    "model",
                    XGBClassifier(
                        n_estimators=cfg["xgb_rounds"],
                        learning_rate=0.03,
                        max_depth=4,
                        min_child_weight=30,
                        device="cuda:0",
                        tree_method="hist",
                        enable_categorical=True,
                        n_jobs=cfg["threads"],
                        random_state=cfg["seed"],
                    ),
                ),
            ]
        ),
    }


def block_intervals(
    y: np.ndarray, scores: dict[str, np.ndarray], weeks: np.ndarray, seed: int, resamples: int
) -> dict[str, Any]:
    unique = np.unique(weeks)
    scorers = {name: _BootstrapScorer(y, p) for name, p in scores.items()}
    positions = {w: np.flatnonzero(weeks == w) for w in unique}
    rng = np.random.default_rng(seed)
    values: dict[str, list[float]] = {name: [] for name in scores}
    for _ in range(resamples):
        selected = rng.choice(unique, size=len(unique), replace=True)
        weights = np.zeros(len(y), dtype=np.int64)
        for week in selected:
            weights[positions[week]] += 1
        for name, scorer in scorers.items():
            values[name].append(scorer.score(weights, ["roc_auc"])["roc_auc"])
    return {
        name: {
            "auc_ci": np.quantile(v, [0.025, 0.975]).tolist(),
            "paired_auc_delta_ci_vs_lightgbm": np.quantile(
                np.asarray(v) - values["lightgbm"], [0.025, 0.975]
            ).tolist(),
        }
        for name, v in values.items()
    }


def run_study(data_root: Path, output: Path, config: Path) -> dict[str, Any]:
    require_tracking_uri()
    if output.exists():
        raise ValueError("output must not exist")
    cfg = yaml.safe_load(config.read_text())
    raw = data_root / "raw"
    acquisition = json.loads((data_root / "acquisition.json").read_text())
    for name, digest in acquisition["files"].items():
        if sha256_file(raw / name) != digest:
            raise ValueError("acquired source bytes changed")
    # This is the only global base-table read: target is excluded explicitly.
    metadata = pq.read_table(raw / "train_base.parquet", columns=["WEEK_NUM", "date_decision"])
    roles = temporal_roles(metadata["WEEK_NUM"].to_pylist(), cfg["shares"], cfg["gap_weeks"])
    output.mkdir(parents=True)
    write_json_atomic(
        output / "temporal-protocol.json",
        {
            "roles": roles,
            "config": cfg,
            "metadata_rows": len(metadata),
            "reserved_labels_opened": False,
        },
    )
    train = load_development(raw, "train", roles["train"], cfg)
    selection = load_development(raw, "selection", roles["selection"], cfg)
    features = cfg["numeric"] + cfg["categorical"]
    scores: dict[str, np.ndarray] = {}
    results: dict[str, Any] = {}
    mlflow.set_experiment("risklab-e8-temporal-development")
    with mlflow.start_run() as run:
        mlflow.log_artifact(str(config), "protocol")
        for name, template in pipelines(cfg).items():
            cv = []
            ordered = roles["train"]
            for fraction in cfg["cv_train_fractions"]:
                cut = int(len(ordered) * fraction)
                end = min(len(ordered), cut + int(len(ordered) * cfg["cv_validation_fraction"]))
                a = train[train.WEEK_NUM.isin(ordered[:cut])]
                valid_weeks = [
                    w for w in ordered[cut:end] if w > ordered[cut - 1] + cfg["gap_weeks"]
                ]
                b = train[train.WEEK_NUM.isin(valid_weeks)]
                pipe = clone(template).fit(a[features], a.target)
                cpu_prediction(pipe, name)
                cv.append(
                    {
                        "train_week_max": int(a.WEEK_NUM.max()),
                        "validation_week_min": int(b.WEEK_NUM.min()),
                        "metrics": compute_metrics(b.target, pipe.predict_proba(b[features])[:, 1]),
                    }
                )
            pipe = clone(template).fit(train[features], train.target)
            cpu_prediction(pipe, name)
            p = np.asarray(pipe.predict_proba(selection[features])[:, 1], dtype=np.float64)
            artifact = output / f"{name}.pkl"
            artifact.write_bytes(pickle.dumps(pipe))
            scores[name] = p
            results[name] = {
                "metrics": compute_metrics(selection.target, p),
                "train_forward_folds": cv,
                "artifact_sha256": sha256_file(artifact),
                "weekly": weekly_metrics(
                    selection.target.to_numpy(), p, selection.WEEK_NUM.to_numpy()
                ),
            }
            mlflow.log_metrics({f"{name}.{k}": v for k, v in results[name]["metrics"].items()})
            print(f"Completed temporal {name}", flush=True)
        report = {
            "run_id": run.info.run_id,
            "config_sha256": sha256_file(config),
            "source_sha256": sha256_file(Path(__file__)),
            "runtime": {**library_versions(), "xgboost": importlib.metadata.version("xgboost")},
            "acquisition_manifest_sha256": sha256_file(data_root / "acquisition.json"),
            "roles": roles,
            "train_rows": len(train),
            "selection_rows": len(selection),
            "predictors": features,
            "results": results,
            "block_bootstrap": block_intervals(
                selection.target.to_numpy(),
                scores,
                selection.WEEK_NUM.to_numpy(),
                cfg["seed"],
                cfg["block_bootstrap_resamples"],
            ),
            "target_roles_read": ["train", "selection"],
            "limitations": [
                "Retrospective development; final holdout labels unopened",
                "Label horizon unknown: four-week buffer is not validated label-maturity purging",
                "11 static predictors; decision-time availability assumed from source dictionary",
                "Customer identity unavailable; cross-period entity overlap cannot be excluded",
                "Few Selection weeks: block intervals have limited precision",
                "No cross-dataset metric/population equivalence to Home Credit 2018",
            ],
        }
        write_json_atomic(output / "summary.json", report)
        mlflow.log_artifact(str(output / "summary.json"))
    print(f"Temporal study complete: {report['run_id']}; final holdout not read", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run_study(args.data_root.resolve(), args.out.resolve(), Path("experiments/e8.yaml").resolve())


if __name__ == "__main__":
    main()
