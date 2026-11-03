"""API contract tests with a locally fitted synthetic model (no MLflow needed)."""

from __future__ import annotations

import pickle

import pytest
from fastapi.testclient import TestClient

from risklab.api.main import create_app
from risklab.api.model_store import ModelStore
from risklab.features.application import load_application_frame
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(app_env, tmp_path_factory):  # type: ignore[no-untyped-def]
    cfg_dir = app_env.config_dir
    X, y = load_application_frame(
        "train",
        "fit",
        data_cfg=app_env.data_cfg,
        partition_cfg=app_env.partition_cfg,
        config_dir=cfg_dir,
    )
    pipe = fit_pipeline(
        make_lgbm_pipeline(load_lgbm_params(cfg_dir), config_dir=str(cfg_dir)),
        X.head(1500),
        y.head(1500),
    )
    path = tmp_path_factory.mktemp("model") / "model.pkl"
    with open(path, "wb") as fh:
        pickle.dump(pipe, fh)
    store = ModelStore()
    store.load_local(path, version="1", threshold=0.1, contract_version="1.0", feature_groups=[])
    from pathlib import Path

    app = create_app(store=store, root=Path(cfg_dir).parent, config_dir=cfg_dir)
    return TestClient(app), X


def test_health_predict_and_contract(client) -> None:  # type: ignore[no-untyped-def]
    http, X = client
    assert http.get("/health").json()["ready"] is True
    row = X.iloc[0].where(lambda s: s.notna(), None).to_dict()
    resp = http.post(
        "/predict", json={"contract_version": "1.0", "application": row, "history": {}}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert 0.0 <= body["probability"] <= 1.0
    assert body["recommendation"] in ("approve", "review")
    bad = http.post("/predict", json={"contract_version": "9.9", "application": row, "history": {}})
    assert bad.status_code == 422
    assert http.get("/models/current").json()["model_version"] == "1"


def test_not_provided_required_source_is_422(app_env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import pickle as pk
    from pathlib import Path

    cfg_dir = app_env.config_dir
    X, y = load_application_frame(
        "train",
        "fit",
        data_cfg=app_env.data_cfg,
        partition_cfg=app_env.partition_cfg,
        config_dir=cfg_dir,
    )
    pipe = fit_pipeline(
        make_lgbm_pipeline(load_lgbm_params(cfg_dir), config_dir=str(cfg_dir)),
        X.head(500),
        y.head(500),
    )
    path = tmp_path / "m.pkl"
    with open(path, "wb") as fh:
        pk.dump(pipe, fh)
    store = ModelStore()
    store.load_local(
        path, version="1", threshold=0.1, contract_version="1.0", feature_groups=["bureau"]
    )
    http = TestClient(create_app(store=store, root=Path(cfg_dir).parent, config_dir=cfg_dir))
    row = X.iloc[0].where(lambda s: s.notna(), None).to_dict()
    resp = http.post(
        "/predict",
        json={
            "contract_version": "1.0",
            "application": row,
            "history": {"bureau": {"status": "not_provided"}},
        },
    )
    assert resp.status_code == 422
    empty = http.post(
        "/predict",
        json={
            "contract_version": "1.0",
            "application": row,
            "history": {"bureau": {"status": "provided", "records": []}},
        },
    )
    assert empty.status_code in (
        200,
        422,
    )  # 200 = no-history result; 422 only if frame assembly rejects
