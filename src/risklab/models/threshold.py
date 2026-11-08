"""Threshold and expected-cost analysis (E4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import Field, model_validator

from risklab.config import _Strict, load_model

COST_FILE = "cost.yaml"


class LoanCostConfig(_Strict):
    lgd: float = Field(gt=0, le=1, allow_inf_nan=False)
    margin: float = Field(gt=0, le=1, allow_inf_nan=False)
    sensitivity_lgd: list[float]
    sensitivity_margin: list[float]


class CostConfig(_Strict):
    cost_fp: float = Field(gt=0, allow_inf_nan=False)
    cost_fn: float = Field(gt=0, allow_inf_nan=False)
    threshold_grid_size: int = Field(default=200, ge=10)
    sensitivity_ratios: list[float] = Field(default_factory=lambda: [2.0, 5.0, 10.0, 20.0])
    policy: Literal["constant", "per_loan"] = "constant"
    loan: LoanCostConfig | None = None

    @model_validator(mode="after")
    def required_loan_assumptions(self) -> CostConfig:
        if self.policy == "per_loan" and self.loan is None:
            raise ValueError("per-loan policy needs configured LGD/margin")
        return self


def load_cost_config(config_dir: Path | str | None = None) -> CostConfig:
    return load_model(config_dir, COST_FILE, CostConfig)


def bayes_threshold(cost_fp: float, cost_fn: float) -> float:
    """Reject when p >= C_FP / (C_FP + C_FN) for calibrated probabilities."""
    return float(cost_fp / (cost_fp + cost_fn))


def expected_cost_at(
    y: np.ndarray,
    p: np.ndarray,
    threshold: float,
    *,
    cost_fp: float,
    cost_fn: float,
    exposure: np.ndarray | None = None,
) -> dict[str, float]:
    y = np.asarray(y)
    p = np.asarray(p, dtype=float)
    pred = (p >= threshold).astype(int)
    fp = float(((pred == 1) & (y == 0)).sum())
    fn = float(((pred == 0) & (y == 1)).sum())
    tp = float(((pred == 1) & (y == 1)).sum())
    tn = float(((pred == 0) & (y == 0)).sum())
    n = float(len(y))
    if n == 0 or p.shape != y.shape:
        raise ValueError("nonempty labels and scores must have matching shape")
    if exposure is None:
        total = cost_fp * fp + cost_fn * fn
    else:
        exposure = np.asarray(exposure, dtype=float)
        if exposure.shape != y.shape or not np.isfinite(exposure).all() or (exposure <= 0).any():
            raise ValueError("per-loan exposure must be finite, positive and match labels")
        total = float(
            np.sum(
                exposure * (cost_fp * ((pred == 1) & (y == 0)) + cost_fn * ((pred == 0) & (y == 1)))
            )
        )
    return {
        "threshold": float(threshold),
        "expected_cost_per_applicant": float(total / n),
        "total_cost": float(total),
        "approval_rate": float((pred == 0).mean()),
        "precision": float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0,
        "recall": float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0,
        "fpr": float(fp / (fp + tn)) if (fp + tn) > 0 else 0.0,
        "fnr": float(fn / (fn + tp)) if (fn + tp) > 0 else 0.0,
    }


def cost_curve(
    y: np.ndarray,
    p: np.ndarray,
    *,
    cost_fp: float,
    cost_fn: float,
    grid_size: int = 200,
    exposure: np.ndarray | None = None,
) -> list[dict[str, float]]:
    grid = np.unique(
        np.concatenate([np.linspace(0.0, 1.0, grid_size), [bayes_threshold(cost_fp, cost_fn)]])
    )
    return [
        expected_cost_at(y, p, float(t), cost_fp=cost_fp, cost_fn=cost_fn, exposure=exposure)
        for t in sorted(grid)
    ]


def choose_threshold(
    y: np.ndarray,
    p: np.ndarray,
    *,
    cost_fp: float,
    cost_fn: float,
    grid_size: int = 200,
    exposure: np.ndarray | None = None,
) -> dict[str, Any]:
    curve = cost_curve(
        y, p, cost_fp=cost_fp, cost_fn=cost_fn, grid_size=grid_size, exposure=exposure
    )
    best = min(curve, key=lambda r: (r["expected_cost_per_applicant"], r["threshold"]))
    bt = bayes_threshold(cost_fp, cost_fn)
    return {
        "threshold": best["threshold"],
        "bayes_threshold": bt,
        "gap_to_bayes": float(best["threshold"] - bt),
        "best": best,
        "curve": curve,
    }


def sensitivity(
    y: np.ndarray, p: np.ndarray, *, cost_fp: float, ratios: list[float], grid_size: int = 200
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in ratios:
        cfn = cost_fp * float(r)
        res = choose_threshold(y, p, cost_fp=cost_fp, cost_fn=cfn, grid_size=grid_size)
        out.append(
            {
                "cost_fn_over_fp": float(r),
                "threshold": res["threshold"],
                "bayes_threshold": res["bayes_threshold"],
                "expected_cost_per_applicant": res["best"]["expected_cost_per_applicant"],
                "approval_rate": res["best"]["approval_rate"],
            }
        )
    return out


__all__ = [
    "CostConfig",
    "bayes_threshold",
    "choose_threshold",
    "cost_curve",
    "expected_cost_at",
    "load_cost_config",
    "sensitivity",
]
