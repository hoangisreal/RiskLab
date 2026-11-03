"""Validated metadata required to serve an evaluated model artifact."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, field_validator

from risklab.config import _Strict
from risklab.manifest import library_versions
from risklab.models.threshold import CostConfig


class ServingConfig(_Strict):
    purpose: Literal["selected_model", "rollback_demo_only"] = "selected_model"
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    calibration_method: Literal["none", "sigmoid", "isotonic"]
    contract_version: str
    feature_groups: list[str]
    feature_columns: list[str] = Field(min_length=1)
    lock_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    partition_hash: str
    data_manifest_hash: str
    git_commit: str
    protocol_hashes: dict[str, str] = Field(min_length=1)
    library_versions: dict[str, str | None]
    selection_metrics: dict[str, float]
    selection_ece: float = Field(ge=0, le=1, allow_inf_nan=False)
    selection_run_id: str
    parity: dict[str, Any]
    cost: CostConfig

    @field_validator("selection_metrics")
    @classmethod
    def valid_metrics(cls, values: dict[str, float]) -> dict[str, float]:
        if set(values) != {"roc_auc", "pr_auc", "brier", "log_loss"}:
            raise ValueError("complete Selection metrics are required")
        if any(not math.isfinite(v) or v < 0 for v in values.values()):
            raise ValueError("Selection metrics must be finite and nonnegative")
        if any(values[k] > 1 for k in ("roc_auc", "pr_auc", "brier")):
            raise ValueError("Selection metric is outside its mathematical range")
        return values

    @field_validator("feature_columns", "feature_groups")
    @classmethod
    def unique_names(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("names must be unique")
        return values


def validate_serving_config(
    config: ServingConfig,
    *,
    expected_contract: str,
    model: Any | None = None,
) -> None:
    if config.contract_version != expected_contract:
        raise ValueError("model contract does not match the API contract")
    current = library_versions()
    if config.library_versions != current or any(v is None for v in current.values()):
        raise ValueError("model library versions do not match this runtime")
    if model is not None:
        names = getattr(model, "feature_names_in_", None)
        if names is None or list(names) != config.feature_columns:
            raise ValueError("model input columns do not match serving_config")
    parity = config.parity
    if (
        parity.get("passed") is not True
        or parity.get("model_sha256") != config.model_sha256
        or parity.get("feature_columns") != config.feature_columns
        or parity.get("rows", 0) < 1
    ):
        raise ValueError("missing or mismatched feature/prediction parity evidence")
