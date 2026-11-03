"""Prediction service: cleaning + shared SQL + calibrated model (no payload logging)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from risklab.api.config import load_api_config
from risklab.api.context import input_spec
from risklab.api.model_store import ModelStore
from risklab.api.schemas import PredictRequest, PredictResponse
from risklab.features.application import frame_from_table
from risklab.features.online import OnlineFeatureError, build_online_features
from risklab.features.registry import FeatureRegistry
from risklab.models.calibrate import predict_proba
from risklab.models.explain import (
    explain_prediction,
    underlying_pipeline,
    unknown_category_warnings,
)


class ServiceError(Exception):
    """HTTP-mapped service failure; ``status`` is the response code."""

    def __init__(self, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def prepare_input(
    application: dict[str, Any],
    history: dict[str, Any],
    *,
    feature_groups: list[str],
    expected: list[str] | None,
    registry: FeatureRegistry,
    root: Path,
    config_dir: Path,
) -> tuple[pd.DataFrame, list[str]]:
    """Build the exact serving matrix, also used by parity verification."""
    schema, cleaning, cols = input_spec(config_dir)
    try:
        hist_frame, warnings = build_online_features(
            application,
            history,
            groups=["application"] + [g for g in feature_groups if g != "application"],
            registry=registry,
            root=root,
            cleaning=cleaning,
            schema=schema,
        )
    except OnlineFeatureError as exc:
        raise ServiceError(str(exc), status=422) from exc
    # Assemble the full model input: application columns + historical groups.
    import pyarrow as pa

    from risklab.data.clean import clean_application

    app_cols = [c.name for c in schema.columns("application_train") if c.name != "TARGET"]
    raw = {c: application.get(c) for c in app_cols}
    raw["SK_ID_CURR"] = 0
    cleaned = clean_application(pa.table({c: [raw[c]] for c in app_cols}), cleaning)
    X = frame_from_table(cleaned, cols, id_column="SK_ID_CURR")
    if len(hist_frame):
        hist_frame.index = X.index
        X = pd.concat([X, hist_frame], axis=1)
    # Reorder to the model's expected columns when fitted names are available.
    if expected is not None:
        missing = [c for c in expected if c not in X.columns]
        if missing:
            raise ServiceError(f"model input columns missing: {missing}", status=503)
        X = X[list(expected)]
    return X, warnings


def predict(
    req: PredictRequest,
    *,
    store: ModelStore,
    registry: FeatureRegistry,
    root: Path,
    config_dir: Path,
    max_records: int | None = None,
) -> PredictResponse:
    loaded = store.current
    if loaded is None:
        raise ServiceError("no model loaded", status=503)
    if req.contract_version != loaded.contract_version:
        raise ServiceError(
            f"contract_version {req.contract_version!r} != served {loaded.contract_version!r}",
            status=422,
        )
    api_config = load_api_config(config_dir)
    max_records = max_records if max_records is not None else api_config.max_records_per_source
    for src, payload in req.history.items():
        if len(payload.records) > max_records:
            raise ServiceError(f"source {src!r} exceeds the {max_records}-record limit", status=422)
    X, warnings = prepare_input(
        req.application,
        {k: v.model_dump() for k, v in req.history.items()},
        feature_groups=loaded.feature_groups,
        expected=list(loaded.model.feature_names_in_)
        if hasattr(loaded.model, "feature_names_in_")
        else None,
        registry=registry,
        root=root,
        config_dir=config_dir,
    )
    prob = float(predict_proba(loaded.model, X)[0])
    warnings.extend(unknown_category_warnings(loaded.model, X))
    explanation = None
    if req.explain:
        explanation = explain_prediction(
            underlying_pipeline(loaded.model), X, top_k=api_config.explanation_top_k
        )
    sources_used = ["application"] + [g for g in loaded.feature_groups if g != "application"]
    return PredictResponse(
        request_id=req.request_id or str(uuid.uuid4()),
        model_version=loaded.version,
        contract_version=loaded.contract_version,
        probability=prob,
        threshold=loaded.threshold,
        recommendation="approve" if prob < loaded.threshold else "review",
        sources_used=sources_used,
        warnings=warnings,
        explanation=explanation,
    )


__all__ = ["ServiceError", "predict"]
