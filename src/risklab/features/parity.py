"""Compare batch and serving features and predictions without opening Test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from risklab.api.service import prepare_input
from risklab.features.historical import load_full_frame
from risklab.features.payload import train_payloads
from risklab.features.registry import load_registry
from risklab.models.calibrate import predict_proba


def verify_parity(
    model: Any,
    groups: list[str],
    *,
    config_dir: Path,
    root: Path,
    sample_size: int,
    seed: int,
    rtol: float,
) -> dict[str, Any]:
    """Check every model input and probability for a seeded sample of Train applicants."""
    registry = load_registry(config_dir)
    frame, _ = load_full_frame("train", "fit", groups, config_dir=config_dir, root=root)
    ids = np.random.default_rng(seed).choice(
        frame.index, size=min(sample_size, len(frame)), replace=False
    )
    columns = list(model.feature_names_in_)
    failed: list[dict[str, Any]] = []
    for position, (applicant, payload) in enumerate(
        train_payloads(applicant_ids=ids.tolist(), groups=groups, config_dir=config_dir, root=root)
    ):
        application, history = payload["application"], payload["history"]
        online, _ = prepare_input(
            application,
            history,
            feature_groups=groups,
            expected=columns,
            registry=registry,
            root=root,
            config_dir=config_dir,
        )
        batch = frame.loc[[applicant], columns]
        bad = []
        numeric = [c for c in columns if pd.api.types.is_numeric_dtype(batch[c])]
        for c in columns:
            a, b = batch.iloc[0][c], online.iloc[0][c]
            equal = (
                (pd.isna(a) and pd.isna(b))
                or (c in numeric and np.isclose(a, b, rtol=rtol, atol=0, equal_nan=True))
                or (c not in numeric and a == b)
            )
            if not equal:
                bad.append(c)
        if bad or not np.allclose(
            predict_proba(model, batch), predict_proba(model, online), rtol=rtol, atol=0
        ):
            # Record positions, never applicant ids or input values, in the evidence.
            failed.append({"sample_position": position, "columns": bad})
    return {
        "passed": not failed,
        "public_schema_verified": True,
        "rows": len(ids),
        "feature_columns": columns,
        "seed": seed,
        "rtol": rtol,
        "failures": failed,
    }
