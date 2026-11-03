"""Independent check of reported metrics. Imports no RiskLab code.

Reads the prediction file written by an experiment and the experiment's ``summary.json``,
recomputes ROC-AUC, PR-AUC, Brier score and log loss with plain scikit-learn, and compares them
with the reported values.

    uv run python scripts/recompute_metrics.py reports/experiments/e1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

TOLERANCE = 1e-9


def recompute(y: pd.Series, p: pd.Series) -> dict[str, float]:
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-15, 1 - 1e-15), labels=[0, 1])),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    directory = Path(argv[1])
    final_result = directory / "result.json"
    if final_result.is_file():
        summary = json.loads(final_result.read_text(encoding="utf-8"))
        preds = pd.read_parquet(directory / "predictions.parquet")
        reported_metrics = {"model": summary["metrics"]}
        rows = summary["rows"]
        print(
            f"Stored evaluation: run={summary.get('run_id')}, "
            f"model_run={summary['model_run_id']}, lock={summary['lock_id']}"
        )
    else:
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        preds = pd.read_parquet(directory / "selection_predictions.parquet")
        if "selection_metrics" in summary:
            reported_metrics = summary["selection_metrics"]
        elif "ablation" in summary:
            reported_metrics = {row["step"]: row["metrics"] for row in summary["ablation"]}
            reported_metrics.update(
                {
                    f"without_{row['removed_group']}": row["metrics_without"]
                    for row in summary.get("leave_one_group_out", [])
                }
            )
        elif "selection" in summary:
            reported_metrics = summary["selection"]["metrics"]
        else:
            raise ValueError("summary contains no supported Selection metrics")
        rows = summary.get("selection_rows", len(preds))
        print(f"Selection runs: {summary.get('run_ids') or summary.get('run_id')}")
        if not (summary.get("run_ids") or summary.get("run_id")):
            raise ValueError("metrics are not reportable without a tracked run")
    if preds["SK_ID_CURR"].duplicated().any() or len(preds) == 0:
        raise ValueError("prediction keys must be unique and nonempty")
    if len(preds) != rows:
        print(f"FAIL: {len(preds)} prediction rows, summary reports {rows}")
        return 1
    failures = 0
    for model, reported in reported_metrics.items():
        again = recompute(preds["TARGET"], preds[model])
        for name, value in again.items():
            diff = abs(value - reported[name])
            status = "ok" if diff <= TOLERANCE else "MISMATCH"
            failures += status != "ok"
            line = f"{model:22s} {name:8s} reported={reported[name]:.10f} "
            print(line + f"recomputed={value:.10f} {status}")
    print("All metrics reproduced." if not failures else f"{failures} mismatch(es).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
