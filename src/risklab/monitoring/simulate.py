"""Monitoring batch simulator from the Monitoring pool (seeded, manifest-registered)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from risklab.manifest import write_json_atomic

Scenario = Literal[
    "control",
    "covariate_young",
    "bug_income_x1000",
    "bug_ext3_missing",
    "bug_category_renamed",
    "label_flip",
]


def make_batch(
    pool: pd.DataFrame, y: pd.Series, *, scenario: Scenario, batch_size: int, seed: int
) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    X = pool.copy()
    yy = y.copy()
    if scenario == "control":
        idx = rng.choice(len(X), size=min(batch_size, len(X)), replace=False)
        return X.iloc[idx].reset_index(drop=True), yy.iloc[idx].reset_index(drop=True)
    if scenario == "covariate_young":
        # Resample toward younger applicants (DAYS_BIRTH closer to zero = younger).
        age = -pd.to_numeric(X["DAYS_BIRTH"], errors="coerce").fillna(-15000)
        w = np.exp(-(age - age.min()) / max(age.std(), 1.0))
        w = w / w.sum()
        idx = rng.choice(len(X), size=min(batch_size, len(X)), replace=True, p=w)
        return X.iloc[idx].reset_index(drop=True), yy.iloc[idx].reset_index(drop=True)
    idx = rng.choice(len(X), size=min(batch_size, len(X)), replace=False)
    Xb, yb = X.iloc[idx].copy().reset_index(drop=True), yy.iloc[idx].copy().reset_index(drop=True)
    if scenario == "bug_income_x1000" and "AMT_INCOME_TOTAL" in Xb.columns:
        Xb["AMT_INCOME_TOTAL"] = Xb["AMT_INCOME_TOTAL"] * 1000.0
    elif scenario == "bug_ext3_missing" and "EXT_SOURCE_3" in Xb.columns:
        mask = rng.random(len(Xb)) < 0.6
        Xb.loc[mask, "EXT_SOURCE_3"] = np.nan
    elif scenario == "bug_category_renamed" and "NAME_INCOME_TYPE" in Xb.columns:
        Xb["NAME_INCOME_TYPE"] = Xb["NAME_INCOME_TYPE"].replace({"Working": "Employed (renamed)"})
    elif scenario == "label_flip":
        flip = rng.random(len(yb)) < 0.1
        yb = yb.copy()
        yb.loc[flip] = 1 - yb.loc[flip]
    return Xb, yb


def register_batch(
    manifest_path: Path, batch_id: str, scenario: str, seed: int, rows: int
) -> dict[str, Any]:
    """Append/replace a batch entry in the monitoring manifest (never a file path API)."""
    import json

    entries: list[dict[str, Any]] = []
    if manifest_path.is_file():
        try:
            doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = list(doc.get("batches", []))
        except (OSError, ValueError):
            entries = []
    entries = [e for e in entries if e.get("batch_id") != batch_id]
    entries.append({"batch_id": batch_id, "scenario": scenario, "seed": seed, "rows": rows})
    doc = {"batches": entries}
    write_json_atomic(manifest_path, doc)
    return doc


__all__ = ["Scenario", "make_batch", "register_batch"]
