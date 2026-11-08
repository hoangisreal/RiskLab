"""E5 challenger study: config validation and pipeline construction."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from risklab.experiments.e5 import build_pipelines, load_e5_config
from risklab.features.application import FeatureColumns

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def test_e5_config_loads_declared_values() -> None:
    cfg = load_e5_config(ROOT / "experiments/e5.yaml")
    assert cfg.decision.min_gain == 0.005
    assert cfg.decision.bootstrap_resamples == 1000
    assert cfg.champion.lgbm_n_estimators == 283
    assert cfg.xgboost.max_depth == 6
    assert cfg.catboost.depth == 6


def test_e5_pipelines_fit_on_small_frame() -> None:
    cfg = load_e5_config(ROOT / "experiments/e5.yaml")
    cols = FeatureColumns(numeric=["AMT_X"], categorical=["SOURCE_KIND"], log=[])
    pipes = build_pipelines(cfg, config_dir=None, columns=cols)
    assert set(pipes) == {
        "lightgbm",
        "xgboost",
        "catboost",
        "hist_gradient_boosting",
        "logistic_regression",
    }
    rng = np.random.default_rng(0)
    X = pd.DataFrame(
        {
            "AMT_X": rng.normal(size=200),
            "SOURCE_KIND": rng.choice(["a", "b"], size=200),
        }
    )
    y = pd.Series((X["AMT_X"] > 0).astype(int))
    for pipe in pipes.values():
        proba = pipe.fit(X, y).predict_proba(X)
        assert proba.shape == (200, 2)
