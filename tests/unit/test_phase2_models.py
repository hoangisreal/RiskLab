"""Unit tests for calibration, threshold, drift, gates (synthetic arrays only)."""

from __future__ import annotations

import numpy as np
import pytest

from risklab.models.calibrate import expected_calibration_error
from risklab.models.threshold import bayes_threshold, choose_threshold, expected_cost_at
from risklab.monitoring.drift import ks_statistic, population_stability_index
from risklab.registry.gates import promotion_gate

pytestmark = pytest.mark.unit


def test_bayes_threshold_and_cost_choice() -> None:
    assert bayes_threshold(1.0, 10.0) == pytest.approx(1 / 11)
    rng = np.random.default_rng(0)
    p = rng.random(2000)
    y = (rng.random(2000) < p).astype(int)
    choice = choose_threshold(y, p, cost_fp=1.0, cost_fn=10.0, grid_size=50)
    assert abs(choice["gap_to_bayes"]) < 0.2
    cost = expected_cost_at(y, p, choice["threshold"], cost_fp=1.0, cost_fn=10.0)
    assert 0.0 <= cost["approval_rate"] <= 1.0


def test_ece_perfect_and_drift_detects_shift() -> None:
    rng = np.random.default_rng(1)
    p = rng.random(5000)
    y = (rng.random(5000) < p).astype(int)
    assert 0.0 <= expected_calibration_error(y, p) <= 0.1
    same = population_stability_index(rng.normal(0, 1, 5000), rng.normal(0, 1, 5000))
    shifted = population_stability_index(rng.normal(0, 1, 5000), rng.normal(2, 1, 5000))
    assert shifted > same
    assert ks_statistic(rng.normal(0, 1, 1000), rng.normal(0, 1, 1000)) < 0.1
    assert ks_statistic(rng.normal(0, 1, 1000), rng.normal(3, 1, 1000)) > 0.5


def test_gate_first_registration_and_champion_compare() -> None:
    ok = promotion_gate(
        {"roc_auc": 0.75, "log_loss": 0.25, "brier": 0.07},
        None,
        ece=0.02,
        contract_valid=True,
        parity_passed=True,
        versions_match=True,
        lock_present=True,
    )
    assert ok["passed"]
    bad = promotion_gate(
        {"roc_auc": 0.60, "log_loss": 0.25, "brier": 0.07},
        None,
        ece=0.02,
        contract_valid=True,
        parity_passed=True,
        versions_match=True,
        lock_present=True,
    )
    assert not bad["passed"]
    worse = promotion_gate(
        {"roc_auc": 0.70, "log_loss": 0.25, "brier": 0.07},
        {"roc_auc": 0.76, "log_loss": 0.25, "brier": 0.07},
        ece=0.02,
        contract_valid=True,
        parity_passed=True,
        versions_match=True,
        lock_present=True,
    )
    assert not worse["passed"]


def test_partial_loan_costs_use_each_applicants_exposure():
    y = np.array([0, 1, 0, 1])
    p = np.array([0.8, 0.1, 0.1, 0.8])
    amounts = np.array([100.0, 1000.0, 100.0, 100.0])
    cost = expected_cost_at(y, p, 0.5, cost_fp=0.05, cost_fn=0.5, exposure=amounts)
    assert cost["total_cost"] == pytest.approx(5 + 500)
    assert cost["expected_cost_per_applicant"] == pytest.approx(505 / 4)
    assert cost["approval_rate"] == 0.5
    for invalid in (np.array([-1.0, 1.0, 1.0, 1.0]), np.array([np.nan, 1.0, 1.0, 1.0])):
        with pytest.raises(ValueError):
            expected_cost_at(y, p, 0.5, cost_fp=0.05, cost_fn=0.5, exposure=invalid)
