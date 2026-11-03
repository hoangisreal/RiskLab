"""Drift metrics against the reference profile: PSI, KS effect size, chi-square.

P-values are deliberately not reported: at monitoring sample sizes they are
uninformative. PSI uses reference-bin proportions with a small epsilon floor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_EPS = 1e-6


def population_stability_index(
    reference: np.ndarray, batch: np.ndarray, *, n_bins: int = 10
) -> float:
    ref = np.asarray(reference, dtype=float)
    bat = np.asarray(batch, dtype=float)
    ref = ref[np.isfinite(ref)]
    bat = bat[np.isfinite(bat)]
    if ref.size == 0 or bat.size == 0:
        return 0.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, n_bins + 1)))
    if edges.size < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    rp = np.histogram(ref, bins=edges)[0].astype(float) / ref.size
    bp = np.histogram(bat, bins=edges)[0].astype(float) / bat.size
    rp = np.clip(rp, _EPS, None)
    bp = np.clip(bp, _EPS, None)
    return float(np.sum((bp - rp) * np.log(bp / rp)))


def ks_statistic(reference: np.ndarray, batch: np.ndarray) -> float:
    ref = np.sort(np.asarray(reference, dtype=float))
    bat = np.sort(np.asarray(batch, dtype=float))
    ref = ref[np.isfinite(ref)]
    bat = bat[np.isfinite(bat)]
    if ref.size == 0 or bat.size == 0:
        return 0.0
    grid = np.concatenate([ref, bat])
    cdf_r = np.searchsorted(ref, grid, side="right") / ref.size
    cdf_b = np.searchsorted(bat, grid, side="right") / bat.size
    return float(np.max(np.abs(cdf_r - cdf_b)))


def categorical_psi(reference: pd.Series, batch: pd.Series) -> float:
    rp = reference.value_counts(normalize=True)
    bp = batch.value_counts(normalize=True)
    cats = rp.index.union(bp.index)
    r = np.array([max(float(rp.get(c, 0.0)), _EPS) for c in cats])
    b = np.array([max(float(bp.get(c, 0.0)), _EPS) for c in cats])
    r = r / r.sum()
    b = b / b.sum()
    return float(np.sum((b - r) * np.log(b / r)))


def missing_rate_change(reference: pd.Series, batch: pd.Series) -> float:
    return float(batch.isna().mean() - reference.isna().mean())


def drift_report(
    reference: pd.DataFrame,
    batch: pd.DataFrame,
    *,
    categorical: list[str] | None = None,
    psi_warn: float = 0.1,
    psi_alert: float = 0.25,
    ks_alert: float = 0.2,
    missing_rate_alert: float = 0.2,
) -> list[dict[str, float | str]]:
    cat = set(categorical or [])
    rows: list[dict[str, float | str]] = []
    for col in reference.columns:
        if col not in batch.columns:
            rows.append(
                {
                    "feature": col,
                    "status": "alert",
                    "psi": 0.0,
                    "ks": 0.0,
                    "missing_rate_change": float(1.0 - reference[col].isna().mean()),
                }
            )
            continue
        if col in cat:
            psi = categorical_psi(reference[col], batch[col])
            ks = 0.0
        else:
            psi = population_stability_index(
                pd.to_numeric(reference[col], errors="coerce").to_numpy(),
                pd.to_numeric(batch[col], errors="coerce").to_numpy(),
            )
            ks = ks_statistic(
                pd.to_numeric(reference[col], errors="coerce").to_numpy(),
                pd.to_numeric(batch[col], errors="coerce").to_numpy(),
            )
        status = (
            "alert"
            if (
                psi >= psi_alert
                or ks >= ks_alert
                or abs(missing_rate_change(reference[col], batch[col])) >= missing_rate_alert
            )
            else ("warn" if psi >= psi_warn else "ok")
        )
        rows.append(
            {
                "feature": col,
                "status": status,
                "psi": float(psi),
                "ks": float(ks),
                "missing_rate_change": missing_rate_change(reference[col], batch[col]),
            }
        )
    return rows


__all__ = [
    "categorical_psi",
    "drift_report",
    "ks_statistic",
    "missing_rate_change",
    "population_stability_index",
]
