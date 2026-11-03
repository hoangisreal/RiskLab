"""Leakage diagnostics on the application model inputs (MASTER_PLAN Section 7.2).

Two checks, neither of which uses published benchmarks:

* Label permutation: labels are shuffled across Train and Selection by row id, the full pipeline is
  fitted on Train and scored on Selection. An AUC distinguishable from 0.5 means evaluation labels
  or rows reach the fit (overlapping rows, or a step that sees evaluation labels). A positive
  control (evaluation rows taken from Train) must exceed the band, otherwise the test has no
  power and the diagnostics do not pass.
* Single-feature screening: univariate ROC-AUC of every input column on Train. A column far from
  0.5 is investigated and documented. Strong known signals (``EXT_SOURCE_*``) are expected.

Selection is used only to score the permuted fits; no model, feature or threshold decision depends
on these numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline

from risklab.config import _Strict
from risklab.models.train import fit_pipeline


class DiagnosticsConfig(_Strict):
    """``configs/experiments/diagnostics.yaml``."""

    seed: int
    permutation_repeats: int = Field(ge=1)
    permutation_band: float = Field(gt=0, lt=0.5)
    screening_threshold: float = Field(gt=0, lt=0.5)
    expected_strong_prefixes: list[str] = Field(default_factory=list)
    top_n: int = Field(ge=1)
    maximum_importance_share: float = Field(default=0.50, gt=0, le=1)
    train_selection_auc_gap: float = Field(default=0.05, gt=0, lt=1)


def label_permutation_test(
    make_pipeline: Callable[[], Pipeline],
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_eval: pd.DataFrame,
    y_eval: pd.Series,
    *,
    repeats: int,
    seed: int,
    band: float,
) -> dict[str, Any]:
    """AUC on the evaluation rows of pipelines fitted with labels permuted over both sets.

    Labels are permuted across the union of the two frames by row id (the frame index), so a row
    that appears in both keeps one label. With disjoint, honest partitions no information links
    Train features to evaluation labels and the AUC is 0.5 up to sampling noise. Overlapping rows
    between the sets, or any step that uses evaluation labels while fitting, push the AUC above
    0.5. Passes when every repeat has ``|AUC - 0.5| < band``.
    """
    labels = pd.concat([y_train, y_eval])
    labels = labels[~labels.index.duplicated()]
    rng = np.random.default_rng(seed)
    aucs: list[float] = []
    for _ in range(repeats):
        permuted = pd.Series(rng.permutation(labels.to_numpy()), index=labels.index)
        y_tr = permuted.loc[X_train.index].rename(y_train.name)
        y_ev = permuted.loc[X_eval.index]
        fitted = fit_pipeline(make_pipeline(), X_train, y_tr)
        aucs.append(float(roc_auc_score(y_ev, fitted.predict_proba(X_eval)[:, 1])))
    worst = max(abs(a - 0.5) for a in aucs)
    return {
        "aucs": aucs,
        "mean_auc": float(np.mean(aucs)),
        "max_abs_deviation": worst,
        "band": band,
        "passed": bool(worst < band),
    }


def single_feature_screen(X: pd.DataFrame, y: pd.Series, categorical: list[str]) -> pd.DataFrame:
    """Univariate ROC-AUC per input column on the rows given (Train only).

    Numeric columns use the value itself on non-missing rows. Categorical columns use each
    level's in-sample positive rate as the score, which is optimistic for high-cardinality
    columns; ``n_levels`` is reported so such columns are read with care. Missing values are not
    scored; ``null_rate`` is reported.
    """
    rows = []
    yy = y.to_numpy()
    for col in X.columns:
        series = X[col]
        mask = series.notna().to_numpy()
        if mask.sum() < 2 or len(np.unique(yy[mask])) < 2:
            continue
        if col in categorical:
            rate = pd.Series(yy[mask]).groupby(series[mask].to_numpy()).transform("mean")
            score = rate.to_numpy()
            n_levels = int(series[mask].nunique())
        else:
            score = series[mask].to_numpy(dtype="float64")
            n_levels = 0
        auc = 0.5 if np.ptp(score) == 0 else float(roc_auc_score(yy[mask], score))
        rows.append(
            {
                "feature": col,
                "auc": auc,
                "abs_deviation": abs(auc - 0.5),
                "null_rate": float(1.0 - mask.mean()),
                "categorical": col in categorical,
                "n_levels": n_levels,
            }
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values(["abs_deviation", "feature"], ascending=[False, True]).reset_index(
        drop=True
    )


def screening_report(
    screen: pd.DataFrame, *, threshold: float, expected_prefixes: list[str], top_n: int
) -> dict[str, Any]:
    """Split features beyond ``threshold`` into expected strong signals and ones to investigate."""
    beyond = screen[screen["abs_deviation"] > threshold]
    expected = beyond[beyond["feature"].str.startswith(tuple(expected_prefixes))]
    unexpected = beyond.drop(expected.index)
    keep = ["feature", "auc", "null_rate", "n_levels"]
    return {
        "threshold": threshold,
        "n_features": int(len(screen)),
        "top": screen.head(top_n)[keep].to_dict(orient="records"),
        "expected_strong": expected[keep].to_dict(orient="records"),
        "to_investigate": unexpected[keep].to_dict(orient="records"),
        "passed": bool(len(unexpected) == 0),
    }


__all__ = [
    "DiagnosticsConfig",
    "label_permutation_test",
    "screening_report",
    "single_feature_screen",
]
