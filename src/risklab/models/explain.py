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
        names = list(prep.get_feature_names_out())
        raw = coef * vals
        # Sum one-hot / indicator columns back to the source feature when possible.
        grouped: dict[str, float] = {}
        for name, value in zip(names, raw, strict=True):
            base_name = str(name).split("__", 1)[-1]
            # Strip one-hot suffix after the column name's last known separator is
            # unreliable; keep the transformed name (documented limitation).
            grouped[base_name] = grouped.get(base_name, 0.0) + float(value)
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
    return {"space": "log_odds_uncalibrated", "base_value": base, "top_contributions": top}


__all__ = ["explain_prediction"]
