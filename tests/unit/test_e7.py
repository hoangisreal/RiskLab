"""Synthetic checks for comparison families and frozen SVM calibration."""

import pickle
import warnings

import numpy as np
import pandas as pd
import pytest
import yaml

from risklab.experiments.e7 import build_pipelines, prepare_prediction
from risklab.features.application import FeatureColumns
from risklab.models.calibrate import fit_calibrator
from tests.conftest import CONFIG_DIR, REPO_ROOT


def pipelines():
    cfg = yaml.safe_load((REPO_ROOT / "experiments/e7.yaml").read_text())
    return build_pipelines(
        cfg,
        CONFIG_DIR,
        FeatureColumns(
            numeric=["AMT_CREDIT", "ratio"], categorical=["category"], log=["AMT_CREDIT"]
        ),
        REPO_ROOT,
    )


def test_declared_families_and_gpu_settings():
    pipes = pipelines()
    assert len(pipes) == 12
    assert "dummy_prior" not in pipes
    assert pipes["xgboost"].named_steps["model"].get_params()["device"] == "cuda:0"
    assert pipes["catboost"].named_steps["model"].get_params()["task_type"] == "GPU"
    assert pipes["linear_svm_sigmoid"].named_steps["model"].get_params()["class_weight"] is None
    assert pipes["mlp"].named_steps["prep"] is not pipes["gaussian_nb"].named_steps["prep"]


@pytest.mark.parametrize(
    "name",
    [
        "decision_tree",
        "random_forest",
        "extra_trees",
        "adaboost",
        "gaussian_nb",
        "mlp",
        "linear_svm_sigmoid",
    ],
)
def test_additional_families_fit_and_serialize_without_learning_from_probe(name):
    train = pd.DataFrame(
        {
            "AMT_CREDIT": np.arange(1, 301) * 1000.0,
            "ratio": [0.1, 0.5, None] * 100,
            "category": ["a", "b", "c"] * 100,
        }
    )
    y = pd.Series([0, 1, 0] * 100)
    pipe = pipelines()[name]
    if name in {"random_forest", "extra_trees", "adaboost"}:
        pipe.set_params(model__n_estimators=3)
    if name == "mlp":
        pipe.set_params(model__max_iter=2, model__batch_size=32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pipe.fit(train, y)
    prepare_prediction(pipe, name, 1)
    fitted_state = pickle.dumps(pipe)
    model = pipe
    if name == "linear_svm_sigmoid":
        cal = train.copy()
        cal["AMT_CREDIT"] += 12345
        model = fit_calibrator(pipe, cal, y, "sigmoid")
        assert pickle.dumps(pipe) == fitted_state
    probe = pd.DataFrame(
        {"AMT_CREDIT": [5000.0, 9000.0], "ratio": [None, 9.0], "category": ["unseen", None]}
    )
    scores = model.predict_proba(probe)
    assert scores.shape == (2, 2) and np.isfinite(scores).all()
    assert (scores >= 0).all() and (scores <= 1).all()
    assert pickle.dumps(pipe) == fitted_state
    np.testing.assert_array_equal(pickle.loads(pickle.dumps(model)).predict_proba(probe), scores)


def test_forest_prediction_reduction_order_is_fixed():
    from sklearn.datasets import make_classification
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import Pipeline

    X, y = make_classification(n_samples=1500, n_features=12, random_state=1)
    pipe = Pipeline(
        [
            (
                "model",
                RandomForestClassifier(
                    n_estimators=200, n_jobs=4, min_samples_leaf=10, random_state=1
                ),
            )
        ]
    ).fit(X, y)
    prepare_prediction(pipe, "random_forest", 1)
    assert pipe.named_steps["model"].n_jobs == 1
    np.testing.assert_array_equal(
        pipe.predict_proba(X)[:32], pickle.loads(pickle.dumps(pipe)).predict_proba(X[:32])
    )
