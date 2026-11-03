from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from risklab.models.evaluate import (
    EvaluationError,
    compute_metrics,
    paired_bootstrap,
    save_predictions,
)

pytestmark = pytest.mark.unit


def test_metrics_match_hand_computed_values() -> None:
    y = [0, 0, 1, 1]
    p = [0.1, 0.4, 0.35, 0.8]
    m = compute_metrics(y, p)
    assert m["roc_auc"] == pytest.approx(0.75)
    assert m["pr_auc"] == pytest.approx(0.8333333333)
    assert m["brier"] == pytest.approx((0.01 + 0.16 + 0.4225 + 0.04) / 4)
    expected_ll = -(math.log(0.9) + math.log(0.6) + math.log(0.35) + math.log(0.8)) / 4
    assert m["log_loss"] == pytest.approx(expected_ll)


def test_perfect_and_constant_scores() -> None:
    y = [0, 0, 0, 1]
    assert compute_metrics(y, [0.0, 0.0, 0.0, 1.0])["roc_auc"] == 1.0
    assert compute_metrics(y, [0.5] * 4)["roc_auc"] == 0.5
    # log loss is finite even for hard 0/1 probabilities
    assert math.isfinite(compute_metrics(y, [1.0, 0.0, 0.0, 0.0])["log_loss"])


@pytest.mark.parametrize(
    ("y", "p"),
    [
        ([0, 1], [0.5]),
        ([], []),
        ([0, 0], [0.1, 0.2]),
        ([0, 2], [0.1, 0.2]),
        ([0, 1], [0.1, float("nan")]),
        ([0, 1], [0.1, 1.2]),
        ([0, 1], [-0.1, 0.2]),
    ],
)
def test_invalid_inputs_are_rejected(y: list[int], p: list[float]) -> None:
    with pytest.raises(EvaluationError):
        compute_metrics(y, p)


def _data(n: int = 2000, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.1).astype(int)
    signal = y * 1.0 + rng.normal(size=n)
    good = 1 / (1 + np.exp(-(signal * 1.5 - 3)))
    weak = 1 / (1 + np.exp(-(rng.normal(size=n) * 0.2 + y * 0.1 - 2.5)))
    return y, good, weak


def test_bootstrap_identical_models_have_zero_difference() -> None:
    y, good, _ = _data()
    res = paired_bootstrap(y, good, good, n_resamples=50, seed=1)
    for m in res.values():
        assert m["delta"] == 0.0
        assert m["ci_low"] == 0.0
        assert m["ci_high"] == 0.0


def test_bootstrap_detects_a_clearly_better_model() -> None:
    y, good, weak = _data()
    res = paired_bootstrap(y, good, weak, n_resamples=200, seed=1)
    assert res["roc_auc"]["delta"] > 0.1
    assert res["roc_auc"]["ci_low"] > 0
    assert res["roc_auc"]["ci_low"] <= res["roc_auc"]["delta"] <= res["roc_auc"]["ci_high"]
    # the full-data values match compute_metrics
    assert res["roc_auc"]["candidate"] == pytest.approx(compute_metrics(y, good)["roc_auc"])


def test_bootstrap_is_deterministic_and_antisymmetric() -> None:
    y, good, weak = _data()
    a = paired_bootstrap(y, good, weak, n_resamples=100, seed=7)
    b = paired_bootstrap(y, good, weak, n_resamples=100, seed=7)
    assert a == b
    c = paired_bootstrap(y, good, weak, n_resamples=100, seed=8)
    assert a["roc_auc"]["ci_low"] != c["roc_auc"]["ci_low"]
    swapped = paired_bootstrap(y, weak, good, n_resamples=100, seed=7)
    assert swapped["roc_auc"]["delta"] == pytest.approx(-a["roc_auc"]["delta"])
    assert swapped["roc_auc"]["ci_low"] == pytest.approx(-a["roc_auc"]["ci_high"])


def test_bootstrap_validates_arguments() -> None:
    y, good, weak = _data(200)
    with pytest.raises(EvaluationError):
        paired_bootstrap(y, good, weak, n_resamples=0, seed=1)
    with pytest.raises(EvaluationError):
        paired_bootstrap(y, good, weak, n_resamples=5, seed=1, ci_level=1.0)
    with pytest.raises(EvaluationError):
        paired_bootstrap(y, good, weak, n_resamples=5, seed=1, metrics=["auc"])
    with pytest.raises(EvaluationError):
        paired_bootstrap(y, good[:10], weak, n_resamples=5, seed=1)


def test_save_predictions_roundtrip(tmp_path: Path) -> None:
    path = save_predictions(
        tmp_path / "sub" / "p.parquet", [3, 1, 2], [0, 1, 0], {"lr": [0.1, 0.9, 0.2]}
    )
    table = pq.read_table(path)
    assert table.column_names == ["SK_ID_CURR", "TARGET", "lr"]
    assert table["SK_ID_CURR"].to_pylist() == [3, 1, 2]
    assert not list((tmp_path / "sub").glob(".*tmp"))
    with pytest.raises(EvaluationError):
        save_predictions(tmp_path / "bad.parquet", [1, 2], [0, 1], {"lr": [0.1]})


@pytest.mark.parametrize("ties", [False, True])
def test_optimized_bootstrap_matches_independent_sklearn_with_duplicate_rows(ties):
    from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

    from risklab.models.evaluate import METRIC_NAMES, _BootstrapScorer

    rng = np.random.default_rng(67)
    y = np.r_[np.zeros(95, dtype=int), np.ones(5, dtype=int)]
    p = rng.random(len(y))
    if ties:
        p = np.round(p, 1)
    scorer = _BootstrapScorer(y, p)
    for _ in range(100):
        idx = rng.integers(0, len(y), size=len(y))
        if len(np.unique(y[idx])) < 2:
            continue
        metrics = scorer.score(np.bincount(idx, minlength=len(y)), METRIC_NAMES)
        clipped = np.clip(p[idx], 1e-15, 1 - 1e-15)
        expected = {
            "roc_auc": roc_auc_score(y[idx], p[idx]),
            "pr_auc": average_precision_score(y[idx], p[idx]),
            "brier": brier_score_loss(y[idx], p[idx]),
            "log_loss": log_loss(y[idx], clipped, labels=[0, 1]),
        }
        assert metrics == pytest.approx(expected, abs=1e-12)
