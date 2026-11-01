from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline

from risklab.models.diagnostics import (
    label_permutation_test,
    screening_report,
    single_feature_screen,
)

pytestmark = pytest.mark.unit


def _frame(n: int, seed: int) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    y = pd.Series((rng.random(n) < 0.2).astype(int), name="TARGET")
    X = pd.DataFrame(
        {
            "signal": y * 0.3 + rng.normal(size=n),
            "noise": rng.normal(size=n),
            "EXT_SOURCE_1": y * 2.0 + rng.normal(size=n),
            "cat": rng.choice(["a", "b", "c"], size=n).astype(object),
        }
    )
    ids = pd.RangeIndex(seed * 100_000, seed * 100_000 + n, name="SK_ID_CURR")
    X.index = ids
    y.index = ids
    return X, y


def _lr() -> Pipeline:
    return make_pipeline(LogisticRegression(max_iter=200))


def _numeric(X: pd.DataFrame) -> pd.DataFrame:
    return X[["signal", "noise", "EXT_SOURCE_1"]]


def test_permutation_on_disjoint_honest_partitions_is_within_band() -> None:
    X_tr, y_tr = _frame(3000, 1)
    X_ev, y_ev = _frame(3000, 2)
    res = label_permutation_test(
        _lr, _numeric(X_tr), y_tr, _numeric(X_ev), y_ev, repeats=3, seed=5, band=0.05
    )
    assert res["passed"]
    assert len(res["aucs"]) == 3


def test_permutation_detects_overlapping_rows() -> None:
    """Positive control: evaluating on rows the model was fitted on must exceed the band."""
    X_tr, y_tr = _frame(1500, 1)

    def memoriser() -> Pipeline:
        return make_pipeline(RandomForestClassifier(n_estimators=50, random_state=0))

    res = label_permutation_test(
        memoriser,
        _numeric(X_tr),
        y_tr,
        _numeric(X_tr.iloc[:500]),
        y_tr.iloc[:500],
        repeats=1,
        seed=5,
        band=0.05,
    )
    assert not res["passed"]
    assert res["mean_auc"] > 0.6


def test_permutation_is_deterministic() -> None:
    X_tr, y_tr = _frame(800, 1)
    X_ev, y_ev = _frame(800, 2)
    args = (_lr, _numeric(X_tr), y_tr, _numeric(X_ev), y_ev)
    a = label_permutation_test(*args, repeats=2, seed=9, band=0.1)
    b = label_permutation_test(*args, repeats=2, seed=9, band=0.1)
    assert a == b


def test_screening_flags_a_feature_that_copies_the_label() -> None:
    X, y = _frame(2000, 1)
    X["leak"] = y.to_numpy() * 1.0
    screen = single_feature_screen(X, y, categorical=["cat"])
    row = screen.set_index("feature").loc["leak"]
    assert row["auc"] == pytest.approx(1.0)
    report = screening_report(screen, threshold=0.2, expected_prefixes=["EXT_SOURCE_"], top_n=3)
    assert [r["feature"] for r in report["to_investigate"]] == ["leak"]
    assert [r["feature"] for r in report["expected_strong"]] == ["EXT_SOURCE_1"]
    assert not report["passed"]
    assert report["top"][0]["feature"] == "leak"


def test_screening_clean_inputs_pass_and_handle_missing_and_constant_columns() -> None:
    X, y = _frame(2000, 1)
    X["const"] = 1.0
    X["mostly_null"] = np.where(np.arange(len(X)) % 50 == 0, 1.0, np.nan)
    screen = single_feature_screen(X.drop(columns=["signal"]), y, categorical=["cat"])
    by = screen.set_index("feature")
    assert by.loc["const", "auc"] == 0.5
    assert by.loc["mostly_null", "null_rate"] == pytest.approx(0.98)
    assert by.loc["cat", "n_levels"] == 3
    report = screening_report(screen, threshold=0.2, expected_prefixes=["EXT_SOURCE_"], top_n=2)
    assert report["passed"]
