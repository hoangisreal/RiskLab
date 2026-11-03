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
    psi_warn: float = 0.1,
    psi_alert: float = 0.25,
    ks_alert: float = 0.2,
    missing_rate_alert: float = 0.2,
    min_n: int = 200,
    min_positives: int = 20,
    seed: int = 0,
    cost_fp: float = 1.0,
    cost_fn: float = 10.0,
    exposure: np.ndarray | None = None,
    subgroup_resamples: int = 500,
    subgroup_ci_level: float = 0.95,
    performance_resamples: int | None = None,
    performance_ci_level: float = 0.95,
) -> dict[str, Any]:
    """Full monitoring report; labeled sections appear only when labels exist."""
    report: dict[str, Any] = {
        "drift": drift_report(
            reference,
            batch,
            categorical=categorical,
            psi_warn=psi_warn,
            psi_alert=psi_alert,
            ks_alert=ks_alert,
            missing_rate_alert=missing_rate_alert,
        ),
        "rows": int(len(batch)),
    }
    if reference_scores is not None and scores_batch is not None:
        from risklab.monitoring.drift import population_stability_index

        report["score_psi"] = population_stability_index(reference_scores, scores_batch)
    if y_batch is not None and scores_batch is not None:
        report["performance"] = performance_report(
            y_batch,
            scores_batch,
            threshold=threshold,
            cost_fp=cost_fp,
            cost_fn=cost_fn,
            exposure=exposure,
            n_resamples=performance_resamples,
            seed=seed,
            ci_level=performance_ci_level,
        )
        if groups:
            report["subgroups"] = subgroup_report(
                batch,
                y_batch,
                scores_batch,
                groups,
                threshold=threshold,
                min_n=min_n,
                min_positives=min_positives,
                seed=seed,
                n_resamples=subgroup_resamples,
                ci_level=subgroup_ci_level,
            )
    alerts = [r for r in report["drift"] if r["status"] == "alert"]
    report["alert_features"] = [str(r["feature"]) for r in alerts]
    report["score_alert"] = report.get("score_psi", 0.0) >= psi_alert
    report["alert"] = bool(alerts or report["score_alert"])
    return report


__all__ = ["evaluate_batch"]
