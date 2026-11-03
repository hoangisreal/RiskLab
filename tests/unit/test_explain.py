"""Explanation sums and feature identities survive encoding and calibration wrappers."""

import numpy as np
import pandas as pd
import pytest

from risklab.features.application import FeatureColumns, load_features_config
from risklab.models.calibrate import fit_calibrator
from risklab.models.explain import (
    explain_prediction,
    underlying_pipeline,
    unknown_category_warnings,
)
from risklab.models.pipelines import (
    load_lgbm_params,
    load_lr_params,
    make_lgbm_pipeline,
    make_lr_pipeline,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def fitted_models():
    X = pd.DataFrame(
        {"AMT_X": np.linspace(1, 5, 80), "SOURCE_KIND": ["with_under_score", "second"] * 40}
    )
    X.loc[1, "AMT_X"] = np.nan
    y = pd.Series(([0] * 20 + [1] * 20) * 2)
    cols = FeatureColumns(numeric=["AMT_X"], categorical=["SOURCE_KIND"], log=[])
    lr = make_lr_pipeline(load_lr_params(), columns=cols).fit(X, y)
    lgbm = make_lgbm_pipeline(
        load_lgbm_params().model_copy(update={"n_estimators": 10, "min_child_samples": 3}),
        cfg=load_features_config().model_copy(update={"lgbm_min_category_count": 2}),
        columns=cols,
    ).fit(X, y)
    return X, y, lr, lgbm


@pytest.mark.parametrize("kind", ["lr", "lgbm"])
def test_contributions_sum_to_raw_log_odds_and_original_feature_names(fitted_models, kind):
    X, _, lr, lgbm = fitted_models
    pipeline = lr if kind == "lr" else lgbm
    row = X.iloc[[0]]
    result = explain_prediction(pipeline, row, top_k=10)
    names = {v["feature"] for v in result["top_contributions"]}
    assert names <= set(X.columns)
    assert result["base_value"] + sum(
        v["contribution"] for v in result["top_contributions"]
    ) == pytest.approx(result["raw_log_odds"])
    probability = pipeline.predict_proba(row)[0, 1]
    assert result["raw_log_odds"] == pytest.approx(
        np.log(probability / (1 - probability)), abs=1e-8
    )


@pytest.mark.parametrize("method", ["none", "sigmoid", "isotonic"])
def test_frozen_calibrator_unwrap_and_unseen_warnings(fitted_models, method):
    X, y, _, pipeline = fitted_models
    calibrated = fit_calibrator(pipeline, X, y, method)
    assert underlying_pipeline(calibrated) is pipeline
    new = X.iloc[[0]].copy()
    new["SOURCE_KIND"] = "not-in-train"
    warnings = unknown_category_warnings(calibrated, new)
    assert len(warnings) == 1
    assert "SOURCE_KIND" in warnings[0]
    assert unknown_category_warnings(calibrated, X.iloc[[0]]) == []
