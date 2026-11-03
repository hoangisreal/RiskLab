"""Drift alerts must cover missing features and mass missingness as well as scores."""

import numpy as np
import pandas as pd
import pytest

from risklab.monitoring.report import evaluate_batch

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mode", ["absent", "all_missing", "sixty_percent_missing"])
def test_missing_external_score_alerts(mode):
    ref = pd.DataFrame({"EXT_SOURCE_3": np.linspace(0, 1, 1000)})
    batch = ref.copy()
    if mode == "absent":
        batch = batch.drop(columns="EXT_SOURCE_3")
    else:
        batch.loc[: 999 if mode == "all_missing" else 599, "EXT_SOURCE_3"] = np.nan
    report = evaluate_batch(ref, batch, None, None, None, threshold=0.1)
    assert report["alert_features"] == ["EXT_SOURCE_3"]
    assert report["alert"]


def test_score_psi_alert_participates_in_batch_alert():
    same = pd.DataFrame({"x": np.linspace(0, 1, 1000)})
    report = evaluate_batch(
        same, same, None, np.linspace(0.7, 1, 1000), np.linspace(0, 1, 1000), threshold=0.1
    )
    assert report["score_alert"]
    assert report["alert"]


def test_subgroup_intervals_and_suppression():
    from risklab.monitoring.performance import subgroup_report

    rng = np.random.default_rng(31)
    p = rng.uniform(0.05, 0.9, 500)
    y = rng.binomial(1, p)
    labels = np.array(["large"] * 490 + ["small"] * 10)
    report = subgroup_report(
        None, y, p, {"group": labels}, threshold=0.1, min_n=100, min_positives=20, n_resamples=100
    )
    small = next(r for r in report if r["level"] == "small")
    large = next(r for r in report if r["level"] == "large")
    assert small["suppressed"]
    assert not large["suppressed"]
    for key in ("auc", "brier", "calibration_in_the_large", "approval_rate", "fpr", "fnr"):
        assert len(large[f"{key}_ci"]) == 2
        assert large[f"{key}_ci"][0] <= large[f"{key}_ci"][1]
