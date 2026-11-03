"""Monitoring report assembly: drift + performance + subgroups for one batch."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from risklab.monitoring.drift import drift_report
from risklab.monitoring.performance import performance_report, subgroup_report


def evaluate_batch(
    reference: pd.DataFrame,
    batch: pd.DataFrame,
    y_batch: np.ndarray | None,
    scores_batch: np.ndarray | None,
    reference_scores: np.ndarray | None,
    *,
    threshold: float,
    categorical: list[str] | None = None,
    groups: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full monitoring report; labeled sections appear only when labels exist."""
    report: dict[str, Any] = {
        "drift": drift_report(reference, batch, categorical=categorical),
        "rows": int(len(batch)),
    }
    if reference_scores is not None and scores_batch is not None:
        from risklab.monitoring.drift import population_stability_index

        report["score_psi"] = population_stability_index(reference_scores, scores_batch)
    if y_batch is not None and scores_batch is not None:
        report["performance"] = performance_report(y_batch, scores_batch, threshold=threshold)
        if groups:
            report["subgroups"] = subgroup_report(
                batch, y_batch, scores_batch, groups, threshold=threshold
            )
    alerts = [r for r in report["drift"] if r["status"] == "alert"]
    report["alert_features"] = [str(r["feature"]) for r in alerts]
    return report


__all__ = ["evaluate_batch"]
