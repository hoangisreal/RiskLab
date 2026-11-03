"""Prediction service: cleaning + shared SQL + calibrated model (no payload logging)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from risklab.api.model_store import ModelStore
from risklab.api.schemas import PredictRequest, PredictResponse
from risklab.config import load_raw_schema
from risklab.data.clean import load_cleaning_config
from risklab.features.application import feature_spec, load_features_config
from risklab.features.online import OnlineFeatureError, build_online_features
from risklab.features.registry import FeatureRegistry
from risklab.models.calibrate import predict_proba
from risklab.models.explain import explain_prediction


class ServiceError(Exception):
    """HTTP-mapped service failure; ``status`` is the response code."""

    def __init__(self, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def predict(
    req: PredictRequest,
    *,
    store: ModelStore,
    registry: FeatureRegistry,
    root: Path,
    config_dir: Path,
    max_records: int = 5000,
) -> PredictResponse:
    loaded = store.current
    if loaded is None:
        raise ServiceError("no model loaded", status=503)
    if req.contract_version != loaded.contract_version:
        raise ServiceError(
            f"contract_version {req.contract_version!r} != served {loaded.contract_version!r}",
            status=422,
        )
    for src, payload in req.history.items():
        if len(payload.records) > max_records:
            raise ServiceError(f"source {src!r} exceeds the {max_records}-record limit", status=422)
    schema = load_raw_schema(config_dir)
    cleaning = load_cleaning_config(config_dir, schema)
    history = {k: v.model_dump() for k, v in req.history.items()}
    try:
        hist_frame, warnings = build_online_features(
            req.application,
            history,
            groups=loaded.feature_groups,
            registry=registry,
            root=root,
            cleaning=cleaning,
            schema=schema,
        )
    except OnlineFeatureError as exc:
        raise ServiceError(str(exc), status=422) from exc
    # Assemble the full model input: application columns + historical groups.
    cols = feature_spec(load_features_config(config_dir), config_dir=config_dir)
    app_row: dict[str, Any] = {}
    import pyarrow as pa

    from risklab.data.clean import clean_application

    app_cols = [c.name for c in schema.columns("application_train") if c.name != "TARGET"]
    raw = {c: req.application.get(c) for c in app_cols}
    raw["SK_ID_CURR"] = 0
    cleaned = clean_application(pa.table({c: [raw[c]] for c in app_cols}), cleaning)
    for name in cols.numeric:
        app_row[name] = (
            float(cleaned[name].to_numpy(zero_copy_only=False)[0])
            if cleaned[name].to_numpy(zero_copy_only=False)[0] is not None
            else float("nan")
        )
    for name in cols.categorical:
        v = cleaned[name].to_pylist()[0]
        app_row[name] = float("nan") if v is None else v
    row = {**app_row}
    if len(hist_frame):
        for c in hist_frame.columns:
            row[c] = (
                float(hist_frame.iloc[0][c]) if pd.notna(hist_frame.iloc[0][c]) else float("nan")
            )
    # Ensure every historical column exists (no-history groups already produce rows).
    X = pd.DataFrame([row])
    # Reorder to the model's expected columns when fitted names are available.
    expected = getattr(loaded.model, "feature_names_in_", None)
    if expected is not None:
        for c in expected:
            if c not in X.columns:
                X[c] = float("nan")
        X = X[list(expected)]
    prob = float(predict_proba(loaded.model, X)[0])
    explanation = None
    if req.explain:
        # Explanations need the uncalibrated pipeline; calibrated wrappers expose it.
        inner = getattr(loaded.model, "estimator", None) or getattr(
            loaded.model, "base_estimator", None
        )
        target = inner if inner is not None and hasattr(inner, "predict_proba") else loaded.model
        try:
            from sklearn.pipeline import Pipeline

            if isinstance(target, Pipeline):
                ex = explain_prediction(target, X)
                explanation = {"space": ex["space"], "top_contributions": ex["top_contributions"]}
        except Exception:
            explanation = None
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
