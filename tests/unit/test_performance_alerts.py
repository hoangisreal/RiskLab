"""Monitoring uses declared tolerances and interval evidence, including concept-only changes."""

import numpy as np
import pytest

from risklab.models.evaluate import bootstrap_metrics, compute_metrics
from risklab.monitoring.performance import performance_alerts, performance_report

pytestmark = pytest.mark.unit


def test_performance_regression_alert_and_unchanged_control():
    rng = np.random.default_rng(18)
    scores = rng.uniform(0.01, 0.99, size=2000)
    labels = (rng.random(len(scores)) < scores).astype(int)
    baseline = compute_metrics(labels, scores)
    settings = dict(
        auc_drop=0.03, log_loss_increase=0.03, brier_increase=0.01, min_auc=0.65, max_log_loss=1.0
    )
    same = performance_report(labels, scores, threshold=0.1, n_resamples=200, seed=3, ci_level=0.99)
    assert performance_alerts(same, baseline, **settings) == []
    flipped = performance_report(
        1 - labels, scores, threshold=0.1, n_resamples=200, seed=3, ci_level=0.99
    )
    assert set(performance_alerts(flipped, baseline, **settings)) == {
        "roc_auc",
        "log_loss",
        "brier",
    }


@pytest.mark.parametrize("stratified", [False, True])
def test_exact_single_model_bootstrap_matches_independent_sklearn(stratified):
    from sklearn.metrics import roc_auc_score

    y = np.array([0, 0, 0, 0, 1, 1])
    p = np.array([0.2, 0.5, 0.5, 0.7, 0.6, 0.9])
    result = bootstrap_metrics(y, p, n_resamples=100, seed=8, ci_level=0.95, stratified=stratified)
    rng = np.random.default_rng(8)
    values = []
    while len(values) < 100:
        idx = (
            np.concatenate(
                [rng.choice(np.flatnonzero(y == c), size=int((y == c).sum())) for c in (0, 1)]
            )
            if stratified
            else rng.integers(0, len(y), len(y))
        )
        if len(np.unique(y[idx])) == 2:
            values.append(roc_auc_score(y[idx], p[idx]))
    assert list(result["roc_auc"].values()) == pytest.approx(np.quantile(values, [0.025, 0.975]))
