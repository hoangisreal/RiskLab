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
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    preds = pd.read_parquet(directory / "selection_predictions.parquet")
    if len(preds) != summary["selection_rows"]:
        print(f"FAIL: {len(preds)} prediction rows, summary reports {summary['selection_rows']}")
        return 1
    failures = 0
    for model, reported in summary["selection_metrics"].items():
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
