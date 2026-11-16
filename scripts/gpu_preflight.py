"""Verify CUDA training on synthetic data without touching project datasets."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import warnings
from pathlib import Path

import numpy as np
from catboost import CatBoostClassifier
from sklearn.datasets import make_classification
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier, build_info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output already exists")
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    X, y = make_classification(n_samples=12000, n_features=40, random_state=20261116)
    X = X.astype(np.float32)
    results = {}
    for name, model in {
        "xgboost": XGBClassifier(
            device="cuda:0", tree_method="hist", n_estimators=40, max_depth=4, n_jobs=4
        ),
        "catboost": CatBoostClassifier(
            task_type="GPU",
            devices="0",
            iterations=40,
            depth=4,
            verbose=False,
            allow_writing_files=False,
            gpu_ram_part=0.6,
        ),
    }.items():
        start = time.monotonic()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            model.fit(X[:10000], y[:10000])
        messages = [str(w.message) for w in caught]
        if any("falling back" in m.lower() or "no visible gpu" in m.lower() for m in messages):
            raise RuntimeError(f"{name} fell back from GPU")
        if name == "xgboost":
            effective = json.loads(model.get_booster().save_config())
            if not effective["learner"]["generic_param"]["device"].startswith("cuda"):
                raise RuntimeError("XGBoost did not use CUDA")
            # CPU prediction is intentional; the fit above must use CUDA.
            model.set_params(device="cpu")
        elif model.get_all_params()["task_type"] != "GPU":
            raise RuntimeError("CatBoost did not train on GPU")
        results[name] = {
            "fit_seconds": round(time.monotonic() - start, 3),
            "synthetic_probe_auc": float(
                roc_auc_score(y[10000:], model.predict_proba(X[10000:])[:, 1])
            ),
            "training_device": "cuda:0",
            "warnings": messages,
        }
    report = {"gpu": gpu, "xgboost_build": build_info(), "synthetic_only": True, "results": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
