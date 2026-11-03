"""The API evaluates a registered simulation and respects the requested pinned model."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from risklab.api.main import create_app
from risklab.api.model_store import LoadedModel, ModelStore
from risklab.features.application import load_application_frame
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline

pytestmark = pytest.mark.integration


def test_monitoring_evaluates_data_and_selects_version(app_env, monkeypatch):
    cfg = app_env.config_dir
    X, y = load_application_frame("train", "fit", config_dir=cfg)
    model = fit_pipeline(
        make_lgbm_pipeline(
            load_lgbm_params(cfg).model_copy(update={"n_estimators": 10}), config_dir=str(cfg)
        ),
        X,
        y,
    )
    store = ModelStore()
    store.swap(LoadedModel("1", model, 0.1, "1.1", [], run_id="synthetic-model"))
    spec = {"batch_id": "missing-score", "scenario": "bug_ext3_missing", "seed": 21, "rows": 100}
    app = create_app(
        store=store, root=cfg.parent, config_dir=cfg, monitoring_batches={"missing-score": spec}
    )
    http = TestClient(app)
    response = http.post("/monitoring/evaluate", json={"batch_id": "missing-score"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["rows"] == 100
    assert "EXT_SOURCE_3" in report["alert_features"]
    assert report["performance"]["roc_auc"] >= 0
    assert report["model_version"] == "1"
    assert report["subgroups"]
    assert http.post("/monitoring/evaluate", json={"batch_id": "unknown"}).status_code == 404
    assert http.post("/monitoring/evaluate", json={"batch_id": "../unsafe"}).status_code == 422
    import risklab.monitoring.service as service

    class Alternate:
        feature_names_in_ = model.feature_names_in_

        def predict_proba(self, frame):
            p = np.full(len(frame), 0.8)
            return np.column_stack([1 - p, p])

    def load_alternate(target_store, *, version, expected_contract, expected_protocol):
        assert version == "2"
        alternate = LoadedModel("2", Alternate(), 0.1, "1.1", [], run_id="synthetic-alternate")
        target_store.swap(alternate)
        return alternate

    monkeypatch.setattr(service, "load_from_registry", load_alternate)
    response = http.post(
        "/monitoring/evaluate", json={"batch_id": "missing-score", "model_version": "2"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["model_version"] == "2"
    assert response.json()["performance"]["brier"] != report["performance"]["brier"]
    assert store.current.version == "1"
    spec.update(batch_id="kaggle", scenario="kaggle_unlabeled")
    app.state.monitoring_batches = {"kaggle": spec}
    response = http.post("/monitoring/evaluate", json={"batch_id": "kaggle"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["labels_available"] is False
    assert "performance" not in report and "subgroups" not in report
    assert report["performance_alerts"] == []
