"""Performance and subgroup monitoring on labeled batches."""

from __future__ import annotations

from typing import Any

import numpy as np

from risklab.models.calibrate import expected_calibration_error
from risklab.models.evaluate import compute_metrics
from risklab.models.threshold import expected_cost_at


def performance_report(
    y: np.ndarray, p: np.ndarray, *, threshold: float, cost_fp: float = 1.0, cost_fn: float = 10.0
) -> dict[str, Any]:
    y = np.asarray(y)
    p = np.asarray(p, dtype=float)
    out: dict[str, Any] = dict(compute_metrics(y, p))
    out["ece"] = expected_calibration_error(y, p)
    out["calibration_in_the_large"] = float(p.mean() - y.mean())
    out["cost"] = expected_cost_at(y, p, threshold, cost_fp=cost_fp, cost_fn=cost_fn)
    return out


def _bootstrap_ci(
    values_fn: Any, y: np.ndarray, p: np.ndarray, *, n: int = 500, seed: int = 0
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    for _ in range(n):
        idx = rng.integers(0, len(y), size=len(y))
        if len(np.unique(y[idx])) < 2:
            continue
        try:
            vals.append(float(values_fn(y[idx], p[idx])))
        except Exception:
            continue
    if not vals:
        return (float("nan"), float("nan"))
    return (float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975)))


def subgroup_report(
    frame: Any,
    y: np.ndarray,
    p: np.ndarray,
    groups: dict[str, Any],
    *,
    threshold: float,
    min_n: int = 200,
    min_positives: int = 20,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Per-group metrics with suppression below ``min_n``/``min_positives``."""
    from sklearn.metrics import brier_score_loss, roc_auc_score

    y = np.asarray(y)
    p = np.asarray(p, dtype=float)
    out: list[dict[str, Any]] = []
    for name, labels in groups.items():
        lab = np.asarray(labels)
        for level in sorted(set(lab.tolist()), key=str):
            m = lab == level
            n, pos = int(m.sum()), int(y[m].sum())
            row: dict[str, Any] = {"group": name, "level": str(level), "n": n, "positives": pos}
            if n < min_n or pos < min_positives or len(np.unique(y[m])) < 2:
                row["suppressed"] = True
                out.append(row)
                continue
            row["suppressed"] = False
            row["auc"] = float(roc_auc_score(y[m], p[m]))
            lo, hi = _bootstrap_ci(lambda yy, pp: roc_auc_score(yy, pp), y[m], p[m], seed=seed)
            row["auc_ci"] = [lo, hi]
            row["brier"] = float(brier_score_loss(y[m], p[m]))
            row["calibration_in_the_large"] = float(p[m].mean() - y[m].mean())
            cost = expected_cost_at(y[m], p[m], threshold, cost_fp=1.0, cost_fn=10.0)
            row["approval_rate"] = cost["approval_rate"]
            row["fpr"] = cost["fpr"]
            row["fnr"] = cost["fnr"]
            out.append(row)
    return out


__all__ = ["performance_report", "subgroup_report"]
