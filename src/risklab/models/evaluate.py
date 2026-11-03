"""Metrics, paired bootstrap comparison and prediction files (MASTER_PLAN Section 6).

Headline metrics for the imbalanced target are ROC-AUC, PR-AUC (average precision), Brier score and
log loss. Accuracy is deliberately not reported. Models are compared with a paired bootstrap: the
same resampled rows score both models, so the interval reflects the difference, not each model's
own sampling noise.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

METRIC_NAMES = ("roc_auc", "pr_auc", "brier", "log_loss")
# Lower is better for these; the others are higher-is-better.
LOWER_IS_BETTER = frozenset({"brier", "log_loss"})
LOG_LOSS_EPS = 1e-15


class EvaluationError(Exception):
    """Raised for invalid labels or scores."""


def _check(y: np.ndarray, p: np.ndarray) -> None:
    if y.shape != p.shape or y.ndim != 1:
        raise EvaluationError(f"labels {y.shape} and scores {p.shape} must be 1-D and equal")
    if y.size == 0:
        raise EvaluationError("empty input")
    if not np.isin(y, (0, 1)).all():
        raise EvaluationError("labels must be 0/1")
    if len(np.unique(y)) < 2:
        raise EvaluationError("labels must contain both classes")
    if not np.isfinite(p).all():
        raise EvaluationError("scores contain NaN or infinity")
    if p.min() < 0.0 or p.max() > 1.0:
        raise EvaluationError("scores must be probabilities in [0, 1]")


def compute_metrics(
    y: Sequence[int] | np.ndarray, p: Sequence[float] | np.ndarray
) -> dict[str, float]:
    """ROC-AUC, PR-AUC, Brier score and log loss of probabilities ``p`` for labels ``y``."""
    yy = np.asarray(y, dtype="int64")
    pp = np.asarray(p, dtype="float64")
    _check(yy, pp)
    return {
        "roc_auc": float(roc_auc_score(yy, pp)),
        "pr_auc": float(average_precision_score(yy, pp)),
        "brier": float(brier_score_loss(yy, pp)),
        "log_loss": float(log_loss(yy, np.clip(pp, LOG_LOSS_EPS, 1 - LOG_LOSS_EPS), labels=[0, 1])),
    }


class _BootstrapScorer:
    """Exact empirical metrics from resampled row counts; ranks are sorted only once."""

    def __init__(self, y: np.ndarray, p: np.ndarray):
        self.y = y
        self.order = np.argsort(p, kind="stable")
        self.groups = np.r_[0, np.flatnonzero(np.diff(p[self.order])) + 1]
        self.brier = (p - y) ** 2
        clipped = np.clip(p, LOG_LOSS_EPS, 1 - LOG_LOSS_EPS)
        self.loss = -(y * np.log(clipped) + (1 - y) * np.log1p(-clipped))

    def score(self, weights: np.ndarray, metrics: Sequence[str]) -> dict[str, float]:
        n = weights.sum()
        result = {}
        if "roc_auc" in metrics or "pr_auc" in metrics:
            positive = np.add.reduceat((weights * self.y)[self.order], self.groups).astype(float)
            negative = np.add.reduceat((weights * (1 - self.y))[self.order], self.groups).astype(
                float
            )
            pos, neg = positive.sum(), negative.sum()
            if pos == 0 or neg == 0:
                raise EvaluationError("bootstrap sample contains a single class")
            if "roc_auc" in metrics:
                result["roc_auc"] = float(
                    np.sum(positive * (np.cumsum(negative) - negative / 2)) / (pos * neg)
                )
            if "pr_auc" in metrics:
                tp = np.cumsum(positive[::-1])
                total = np.cumsum((positive + negative)[::-1])
                precision = np.divide(tp, total, out=np.zeros_like(tp), where=total != 0)
                result["pr_auc"] = float(np.sum(positive[::-1] * precision) / pos)
        if "brier" in metrics:
            result["brier"] = float(np.sum(weights * self.brier) / n)
        if "log_loss" in metrics:
            result["log_loss"] = float(np.sum(weights * self.loss) / n)
        return result


def paired_bootstrap(
    y: Sequence[int] | np.ndarray,
    p_candidate: Sequence[float] | np.ndarray,
    p_reference: Sequence[float] | np.ndarray,
    *,
    n_resamples: int,
    seed: int,
    ci_level: float = 0.95,
    metrics: Sequence[str] = METRIC_NAMES,
) -> dict[str, dict[str, float]]:
    """Paired bootstrap of ``metric(candidate) - metric(reference)`` for each metric.

    Rows are resampled with replacement, identically for both models. Returns, per metric, the
    point difference on the full data and the percentile interval of the resampled differences.
    A resample that contains a single class is redrawn so every metric is defined.
    """
    yy = np.asarray(y, dtype="int64")
    pc = np.asarray(p_candidate, dtype="float64")
    pr = np.asarray(p_reference, dtype="float64")
    _check(yy, pc)
    _check(yy, pr)
    unknown = sorted(set(metrics) - set(METRIC_NAMES))
    if unknown:
        raise EvaluationError(f"unknown metric(s) {unknown}")
    if n_resamples < 1:
        raise EvaluationError("n_resamples must be >= 1")
    if not 0.0 < ci_level < 1.0:
        raise EvaluationError("ci_level must be in (0, 1)")

    full_c, full_r = compute_metrics(yy, pc), compute_metrics(yy, pr)
    rng = np.random.default_rng(seed)
    n = yy.size
    candidate_scorer, reference_scorer = _BootstrapScorer(yy, pc), _BootstrapScorer(yy, pr)
    deltas: dict[str, list[float]] = {m: [] for m in metrics}
    done = 0
    while done < n_resamples:
        idx = rng.integers(0, n, size=n)
        ys = yy[idx]
        if ys.min() == ys.max():
            continue
        weights = np.bincount(idx, minlength=n)
        mc, mr = candidate_scorer.score(weights, metrics), reference_scorer.score(weights, metrics)
        for m in metrics:
            deltas[m].append(mc[m] - mr[m])
        done += 1

    alpha = (1.0 - ci_level) / 2.0
    out: dict[str, dict[str, float]] = {}
    for m in metrics:
        arr = np.asarray(deltas[m])
        out[m] = {
            "candidate": full_c[m],
            "reference": full_r[m],
            "delta": full_c[m] - full_r[m],
            "ci_low": float(np.quantile(arr, alpha)),
            "ci_high": float(np.quantile(arr, 1.0 - alpha)),
        }
    return out


def bootstrap_metrics(
    y: np.ndarray,
    p: np.ndarray,
    *,
    n_resamples: int,
    seed: int,
    ci_level: float,
    stratified: bool = True,
) -> dict[str, dict[str, float]]:
    """Percentile intervals for one locked model; no fitting or model decisions."""
    _check(y, p)
    if n_resamples < 1 or not 0 < ci_level < 1:
        raise EvaluationError("invalid bootstrap settings")
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = {k: [] for k in METRIC_NAMES}
    scorer = _BootstrapScorer(y, p)
    done = 0
    while done < n_resamples:
        # Stratified resampling keeps each class represented, including small fixtures.
        idx = (
            np.concatenate(
                [
                    rng.choice(np.flatnonzero(y == c), size=int((y == c).sum()), replace=True)
                    for c in (0, 1)
                ]
            )
            if stratified
            else rng.integers(0, len(y), size=len(y))
        )
        if len(np.unique(y[idx])) < 2:
            continue
        for key, value in scorer.score(np.bincount(idx, minlength=len(y)), METRIC_NAMES).items():
            samples[key].append(value)
        done += 1
    alpha = (1 - ci_level) / 2
    return {
        k: {"ci_low": float(np.quantile(v, alpha)), "ci_high": float(np.quantile(v, 1 - alpha))}
        for k, v in samples.items()
    }


def save_predictions(
    path: Path | str,
    ids: Sequence[int] | np.ndarray,
    y: Sequence[int] | np.ndarray,
    scores: Mapping[str, Sequence[float] | np.ndarray],
    *,
    id_column: str = "SK_ID_CURR",
    target_column: str = "TARGET",
) -> Path:
    """Write row-level predictions (id, label, one column per named score) as Parquet.

    These files hold labels and are never committed (``reports/`` is gitignored). They let anyone
    recompute the reported metrics without any RiskLab code (``scripts/recompute_metrics.py``).
    """
    ids_a = np.asarray(ids, dtype="int64")
    y_a = np.asarray(y, dtype="int64")
    columns: dict[str, Any] = {id_column: ids_a, target_column: y_a}
    for name, values in scores.items():
        arr = np.asarray(values, dtype="float64")
        if arr.shape != ids_a.shape:
            raise EvaluationError(f"score {name!r} has shape {arr.shape}, expected {ids_a.shape}")
        columns[name] = arr
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        pq.write_table(pa.table(columns), tmp)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


__all__ = [
    "LOWER_IS_BETTER",
    "METRIC_NAMES",
    "EvaluationError",
    "compute_metrics",
    "paired_bootstrap",
    "save_predictions",
]
