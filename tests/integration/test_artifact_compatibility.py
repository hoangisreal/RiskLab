"""Characterize Python import identities and fresh-process synthetic artifact loading."""

from __future__ import annotations

import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from risklab.features.application import FeatureColumns, load_features_config
from risklab.models.calibrate import fit_calibrator, predict_proba
from risklab.models.pipelines import (
    CategoryCaster,
    _log1p_nonneg,
    load_lgbm_params,
    load_lr_params,
    make_lgbm_pipeline,
    make_lr_pipeline,
)
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "name,value", [("CategoryCaster", CategoryCaster), ("_log1p_nonneg", _log1p_nonneg)]
)
def test_legacy_pickle_global_remains_importable(name, value) -> None:
    # A same-process roundtrip alone can miss renamed globals referenced by old artifacts.
    legacy_global = f"crisklab.models.pipelines\n{name}\n.".encode("ascii")
    assert pickle.loads(legacy_global) is value


@pytest.mark.parametrize("kind", ["lr", "lgbm"])
@pytest.mark.parametrize("method", ["none", "sigmoid", "isotonic"])
def test_synthetic_pipeline_loads_in_fresh_process(tmp_path: Path, kind: str, method: str) -> None:
    # Small test-only data: separate fit/calibration/probe frames, no repository partitions.
    train = pd.DataFrame(
        {
            "AMT_CREDIT": np.arange(1, 41, dtype=float) * 1000,
            "ratio": [None, 0.1, 0.6, 0.9] * 10,
            "category": ["a", "b", "a", "b"] * 10,
        }
    )
    labels = pd.Series([0, 0, 1, 1] * 10)
    calibration = train.copy()
    calibration["AMT_CREDIT"] += 123
    probe = pd.DataFrame(
        {"AMT_CREDIT": [7000.0, 15000.0], "ratio": [None, 0.5], "category": ["unseen", None]}
    )
    columns = FeatureColumns(
        numeric=["AMT_CREDIT", "ratio"], categorical=["category"], log=["AMT_CREDIT"]
    )
    cfg = load_features_config(CONFIG_DIR).model_copy(update={"lgbm_min_category_count": 1})
    if kind == "lr":
        pipeline = make_lr_pipeline(load_lr_params(CONFIG_DIR), cfg, columns=columns)
    else:
        params = load_lgbm_params(CONFIG_DIR).model_copy(
            update={"n_estimators": 4, "min_child_samples": 2, "n_jobs": 1}
        )
        pipeline = make_lgbm_pipeline(params, cfg, columns=columns)
    pipeline.fit(train, labels)
    model = fit_calibrator(pipeline, calibration, labels, method)
    expected = predict_proba(model, probe)
    artifact = tmp_path / "synthetic.pkl"
    artifact.write_bytes(pickle.dumps(model))
    payload = tmp_path / "probe.json"
    payload.write_text(probe.to_json(orient="split"))
    script = """
import json, pickle, sys
import pandas as pd
from risklab.models.calibrate import predict_proba
with open(sys.argv[1], 'rb') as stream:
    model = pickle.load(stream)
frame = pd.read_json(sys.argv[2], orient='split')
print(json.dumps({'columns': list(model.feature_names_in_),
                  'probabilities': predict_proba(model, frame).tolist()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(artifact), str(payload)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    restored = json.loads(result.stdout)
    assert restored["columns"] == list(probe.columns)
    np.testing.assert_array_equal(restored["probabilities"], expected)
