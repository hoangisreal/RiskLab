"""Calibration on frozen pipelines (MASTER_PLAN Section 5 steps 2-3).

The base pipeline is fitted on Train and frozen; each calibrator is fitted only on
Calibration. Method choice happens on Selection by log loss with a paired bootstrap
and the simpler-method tie rule (none < sigmoid < isotonic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.pipeline import Pipeline

from risklab.config import _Strict, load_model
from risklab.models.evaluate import compute_metrics, paired_bootstrap

CALIBRATION_FILE = "calibration.yaml"
METHOD_ORDER: tuple[str, ...] = ("none", "sigmoid", "isotonic")
Method = Literal["none", "sigmoid", "isotonic"]


class CalibrationConfig(_Strict):
    methods: list[str] = Field(default_factory=lambda: list(METHOD_ORDER))
    ece_bins: int = Field(default=15, ge=2)
    bootstrap_resamples: int = Field(default=1000, ge=100)
    bootstrap_seed: int = 20261101
    ci_level: float = Field(default=0.95, gt=0, lt=1)


def load_calibration_config(config_dir: Path | str | None = None) -> CalibrationConfig:
    return load_model(config_dir, CALIBRATION_FILE, CalibrationConfig)


def expected_calibration_error(y: np.ndarray, p: np.ndarray, *, n_bins: int = 15) -> float:
    """ECE with equal-frequency bins (edges from quantiles of ``p``)."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if y.shape != p.shape or y.size == 0:
        raise ValueError("y and p must be non-empty with equal shape")
    edges = np.quantile(p, np.linspace(0, 1, n_bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        m = idx == b
        if m.any():
            ece += float(m.mean()) * abs(float(y[m].mean()) - float(p[m].mean()))
    return float(ece)


def fit_calibrator(
    fitted_pipeline: Pipeline, X_cal: pd.DataFrame, y_cal: pd.Series, method: str
) -> Any:
    """Fit one calibrator on Calibration; ``none`` returns the frozen pipeline itself."""
    if method == "none":
        return fitted_pipeline
    if method not in ("sigmoid", "isotonic"):
        raise ValueError(f"unknown calibration method {method!r}")
    cal = CalibratedClassifierCV(FrozenEstimator(fitted_pipeline), method=method)
    cal.fit(X_cal, y_cal)
    return cal


def predict_proba(model: Any, X: pd.DataFrame) -> np.ndarray:
    out: np.ndarray = model.predict_proba(X)[:, 1]
    return out


def calibration_table(y: np.ndarray, p: np.ndarray, *, n_bins: int = 15) -> list[dict[str, float]]:
    """Reliability table (mean predicted vs observed rate per equal-frequency bin)."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.quantile(p, np.linspace(0, 1, n_bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, n_bins - 1)
    rows: list[dict[str, float]] = []
    for b in range(n_bins):
        m = idx == b
        if m.any():
            rows.append(
                {
                    "bin": float(b),
                    "n": float(m.sum()),
                    "mean_predicted": float(p[m].mean()),
                    "observed_rate": float(y[m].mean()),
                }
            )
    return rows


def select_calibration_method(
    y_sel: np.ndarray,
    scores: dict[str, np.ndarray],
    *,
    config: CalibrationConfig,
) -> dict[str, Any]:
    """Choose the method by Selection log loss; simpler wins when its CI includes zero."""
    metrics = {m: compute_metrics(y_sel, s) for m, s in scores.items()}
    ece = {
        m: expected_calibration_error(y_sel, s, n_bins=config.ece_bins) for m, s in scores.items()
    }
    ordered = [m for m in METHOD_ORDER if m in scores]
    best = min(ordered, key=lambda m: metrics[m]["log_loss"])
    # Compare best against each simpler method; if any simpler is not significantly
    # worse (CI of simpler-minus-best includes zero for log loss), prefer the simpler.
    chosen = best
    for simpler in ordered:
        if METHOD_ORDER.index(simpler) >= METHOD_ORDER.index(best):
            continue
        boot = paired_bootstrap(
            y_sel,
            scores[simpler],
            scores[best],
            n_resamples=config.bootstrap_resamples,
            seed=config.bootstrap_seed,
            ci_level=config.ci_level,
            metrics=["log_loss"],
        )["log_loss"]
        # delta = simpler - best (lower better); CI includes 0 => no significant loss.
        if boot["ci_low"] <= 0.0 <= boot["ci_high"]:
            chosen = simpler
            break
    return {"metrics": metrics, "ece": ece, "best_by_log_loss": best, "chosen": chosen}


__all__ = [
    "METHOD_ORDER",
    "CalibrationConfig",
    "calibration_table",
    "expected_calibration_error",
    "fit_calibrator",
    "load_calibration_config",
    "predict_proba",
    "select_calibration_method",
]
