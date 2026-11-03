"""API request/response schemas (MASTER_PLAN Section 10.2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HistorySource(BaseModel):
    status: Literal["provided", "not_provided"] = "not_provided"
    records: list[dict[str, Any]] = Field(default_factory=list)


class PredictRequest(BaseModel):
    contract_version: str = "1.0"
    request_id: str | None = None
    application: dict[str, Any]
    history: dict[str, HistorySource] = Field(default_factory=dict)
    explain: bool = False


class Explanation(BaseModel):
    space: str = "log_odds_uncalibrated"
    top_contributions: list[dict[str, Any]] = Field(default_factory=list)


class PredictResponse(BaseModel):
    request_id: str | None = None
    model_version: str
    contract_version: str
    probability: float
    threshold: float
    recommendation: Literal["approve", "review"]
    sources_used: list[str]
    warnings: list[str] = Field(default_factory=list)
    explanation: Explanation | None = None


class MonitoringRequest(BaseModel):
    batch_id: str
    model_version: str | None = None


__all__ = ["Explanation", "HistorySource", "MonitoringRequest", "PredictRequest", "PredictResponse"]
