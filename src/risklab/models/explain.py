"""Explanations of the uncalibrated model's log-odds contributions.

LightGBM uses ``pred_contrib=True`` (TreeSHAP); Logistic Regression uses
coefficient x transformed value with one-hot contributions summed back to the
original feature. Contributions describe the uncalibrated model only; they are
not causal effects or adverse-action reasons.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline


def underlying_pipeline(model: Any) -> Pipeline:
    """Unwrap frozen calibration wrappers without fitting or changing the estimator."""
    seen: set[int] = set()
    while not isinstance(model, Pipeline):
        if id(model) in seen:
            raise ValueError("cyclic estimator wrapper")
        seen.add(id(model))
        next_model = getattr(model, "estimator", None)
        if next_model is None:
            raise ValueError("model does not expose an explainable fitted pipeline")
        model = next_model
    return model


def unknown_category_warnings(model: Any, frame: pd.DataFrame) -> list[str]:
    pipeline = underlying_pipeline(model)
    prep = pipeline.named_steps["prep"]
    messages = []
    for _, transformer, columns in prep.transformers_:
        categories = getattr(transformer, "categories_", None)
        if categories is None:
            continue
        for name, known in zip(columns, categories, strict=True):
            value = frame.iloc[0][name]
            if pd.notna(value) and value not in known:
                messages.append(
                    f"categorical field {name!r} is unseen or infrequent in Train; "
                    "unknown handling applied"
                )
    return messages


def _original_names(prep: Any) -> list[str]:
    names: list[str] = []
    for _, transformer, columns in prep.transformers_:
        if transformer == "drop" or not len(columns):
            continue
        if isinstance(transformer, str):
            names.extend(columns)
        elif hasattr(transformer, "categories_"):
            infrequent = getattr(transformer, "infrequent_categories_", [None] * len(columns))
            dropped = getattr(transformer, "drop_idx_", None)
            for index, (column, categories) in enumerate(
                zip(columns, transformer.categories_, strict=True)
            ):
                count = len(categories)
                if infrequent[index] is not None:
                    count -= len(infrequent[index]) - 1
                if dropped is not None and dropped[index] is not None:
                    count -= 1
                names.extend([column] * count)
        else:
            for transformed in transformer.get_feature_names_out(columns):
                name = str(transformed)
                if name.startswith("missingindicator_"):
                    name = name.removeprefix("missingindicator_")
                if name not in columns:
                    raise ValueError("cannot map transformed contribution to its original feature")
                names.append(name)
    return names


def explain_prediction(
    pipeline: Pipeline, X_row: pd.DataFrame, *, top_k: int = 5
) -> dict[str, Any]:
    """Top contributions for a single-row frame through a fitted pipeline."""
    if len(X_row) != 1:
        raise ValueError("explain_prediction expects exactly one row")
    model = pipeline.named_steps["model"]
    prep = pipeline.named_steps["prep"]
    Xt = prep.transform(X_row)
    type_name = type(model).__name__
    if type_name == "LGBMClassifier":
        booster = model.booster_
        # LightGBM contrib includes the base value as the last column.
        contrib = np.asarray(booster.predict(Xt, pred_contrib=True))[0]
        names = (
            list(Xt.columns) if hasattr(Xt, "columns") else [f"f{i}" for i in range(Xt.shape[1])]
        )
        pairs = sorted(zip(names, contrib[:-1], strict=True), key=lambda kv: -abs(float(kv[1])))
        base = float(contrib[-1])
    else:
        coef = np.asarray(model.coef_, dtype=float).ravel()
        vals = (
            np.asarray(Xt, dtype=float).ravel()
            if not hasattr(Xt, "to_numpy")
            else np.asarray(Xt.to_numpy(), dtype=float).ravel()
        )
        names = _original_names(prep)
        raw = coef * vals
        grouped: dict[str, float] = {}
        for name, value in zip(names, raw, strict=True):
            grouped[name] = grouped.get(name, 0.0) + float(value)
        pairs = sorted(grouped.items(), key=lambda kv: -abs(kv[1]))
        base = float(np.asarray(model.intercept_).ravel()[0])
    top = [{"feature": str(n), "contribution": float(v)} for n, v in pairs[:top_k]]
    # Attach the raw input value when the feature maps directly to an input column.
    for item in top:
        if item["feature"] in X_row.columns:
            v = X_row.iloc[0][item["feature"]]
            item["value"] = (
                None
                if pd.isna(v)
                else (float(v) if isinstance(v, (int, float, np.floating)) else str(v))
            )
    total = float(sum(float(value) for _, value in pairs))
    return {
        "space": "log_odds_uncalibrated",
        "base_value": base,
        "raw_log_odds": base + total,
        "top_contributions": top,
    }


__all__ = ["explain_prediction"]
