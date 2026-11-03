"""Promotion gate on Selection (never Test): challenger vs champion."""

from __future__ import annotations

from typing import Any

GATE_DEFAULTS: dict[str, float] = {
    "auc_tolerance": 0.005,
    "logloss_tolerance": 0.01,
    "brier_tolerance": 0.005,
    "ece_limit": 0.05,
    "floor_roc_auc": 0.70,
    "floor_log_loss": 0.30,
}


def promotion_gate(
    challenger: dict[str, float],
    champion: dict[str, float] | None,
    *,
    ece: float,
    contract_valid: bool,
    parity_passed: bool,
    versions_match: bool,
    lock_present: bool,
    config: dict[str, float] | None = None,
) -> dict[str, Any]:
    cfg = {**GATE_DEFAULTS, **(config or {})}
    checks: dict[str, bool] = {
        "contract_valid": bool(contract_valid),
        "parity_passed": bool(parity_passed),
        "versions_match": bool(versions_match),
        "lock_present": bool(lock_present),
        "ece_within_limit": bool(ece <= cfg["ece_limit"]),
    }
    if champion is None:
        checks["roc_auc_floor"] = bool(challenger.get("roc_auc", 0.0) >= cfg["floor_roc_auc"])
        checks["log_loss_floor"] = bool(challenger.get("log_loss", 9e9) <= cfg["floor_log_loss"])
    else:
        checks["roc_auc_not_worse"] = bool(
            challenger.get("roc_auc", 0.0) >= champion.get("roc_auc", 0.0) - cfg["auc_tolerance"]
        )
        checks["log_loss_not_worse"] = bool(
            challenger.get("log_loss", 9e9)
            <= champion.get("log_loss", 0.0) + cfg["logloss_tolerance"]
        )
        checks["brier_not_worse"] = bool(
            challenger.get("brier", 9e9) <= champion.get("brier", 0.0) + cfg["brier_tolerance"]
        )
    passed = all(checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "challenger": challenger,
        "champion": champion,
        "ece": ece,
    }


__all__ = ["GATE_DEFAULTS", "promotion_gate"]
