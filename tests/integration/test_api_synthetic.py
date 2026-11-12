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
    store.load_local(path, version="1", threshold=0.1, contract_version="1.1", feature_groups=[])
    from pathlib import Path

    app = create_app(store=store, root=Path(cfg_dir).parent, config_dir=cfg_dir)
    return TestClient(app), X


def test_health_predict_and_contract(client) -> None:  # type: ignore[no-untyped-def]
    http, X = client
    assert http.get("/health").json()["ready"] is True
    row = X.iloc[0].where(lambda s: s.notna(), None).to_dict()
    row.pop("DAYS_EMPLOYED_IS_SENTINEL", None)
    resp = http.post(
        "/predict", json={"contract_version": "1.1", "application": row, "history": {}}
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
        path, version="1", threshold=0.1, contract_version="1.1", feature_groups=["bureau"]
    )
    http = TestClient(create_app(store=store, root=Path(cfg_dir).parent, config_dir=cfg_dir))
    row = X.iloc[0].where(lambda s: s.notna(), None).to_dict()
    row.pop("DAYS_EMPLOYED_IS_SENTINEL", None)
    resp = http.post(
        "/predict",
        json={
            "contract_version": "1.1",
            "application": row,
            "history": {"bureau": {"status": "not_provided"}},
        },
    )
    assert resp.status_code == 422
    empty = http.post(
        "/predict",
        json={
            "contract_version": "1.1",
            "application": row,
            "history": {"bureau": {"status": "provided", "records": []}},
        },
    )
    assert empty.status_code == 200, empty.text


@pytest.mark.parametrize(
    "column,value",
    [
        ("AMT_CREDIT", -1),
        ("AMT_INCOME_TOTAL", 0),
        ("AMT_CREDIT", "100"),
        ("AMT_CREDIT", True),
        ("DAYS_BIRTH", 1),
        ("FLAG_MOBIL", 0.5),
        ("REGION_RATING_CLIENT", -1),
        ("EXT_SOURCE_3", 1.1),
    ],
)
def test_public_numeric_types_and_ranges(client, column, value):
    http, _ = client
    application = {"AMT_CREDIT": 100000.0, "AMT_INCOME_TOTAL": 100000.0, column: value}
    response = http.post("/predict", json={"application": application})
    assert response.status_code == 422, response.text
    assert all("input" not in detail for detail in response.json()["detail"])


def test_schema_forbids_identifiers_and_computed_features(client):
    http, _ = client
    for field in ("SK_ID_CURR", "TARGET", "CODE_GENDER", "DAYS_EMPLOYED_IS_SENTINEL"):
        response = http.post(
            "/predict",
            json={"application": {"AMT_CREDIT": 100000, "AMT_INCOME_TOTAL": 100000, field: 1}},
        )
        assert response.status_code == 422
    schema = http.get("/openapi.json").json()
    fields = schema["components"]["schemas"]["Application"]["properties"]
    assert fields["AMT_CREDIT"]["type"] == "number"
    assert "TARGET" not in fields
    assert "SK_ID_CURR" not in fields
    assert "NAME_CONTRACT_TYPE" in fields


def test_declared_and_chunked_body_limits_and_metadata_only_logs(client, monkeypatch, caplog):
    import logging

    import risklab.api.main as api_main
    from risklab.api.config import load_api_config
    from tests.conftest import CONFIG_DIR, REPO_ROOT

    http, _ = client
    cfg = load_api_config(CONFIG_DIR).model_copy(update={"max_body_bytes": 512})
    monkeypatch.setattr(api_main, "load_api_config", lambda _: cfg)
    limited = TestClient(
        create_app(store=http.app.state.store, root=REPO_ROOT, config_dir=CONFIG_DIR)
    )
    assert limited.post("/predict", content=b" " * 513).status_code == 413
    assert limited.post("/predict", content=iter([b" " * 256, b" " * 257])).status_code == 413
    with caplog.at_level(logging.INFO, logger="risklab.api"):
        response = http.post(
            "/predict",
            json={
                "application": {
                    "AMT_CREDIT": 100000,
                    "AMT_INCOME_TOTAL": 100000,
                    "NAME_INCOME_TYPE": "payload-must-not-be-logged",
                }
            },
        )
    assert response.status_code == 200, response.text
    assert "payload-must-not-be-logged" not in caplog.text
    assert "model_version" in caplog.text
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_request_keeps_model_snapshot_during_concurrent_reload(client, monkeypatch):
    import risklab.api.main as api_main
    from risklab.api.model_store import LoadedModel

    http, _ = client
    store = http.app.state.store
    original = store.current
    replacement = LoadedModel("replacement", original.model, 1.0, "1.1", [])

    def reload_during_validation(history, groups):
        store.swap(replacement)

    monkeypatch.setattr(api_main, "validate_history_records", reload_during_validation)
    payload = {"application": {"AMT_CREDIT": 100000, "AMT_INCOME_TOTAL": 100000}}
    try:
        response = http.post("/predict", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["model_version"] == original.version
        assert response.json()["threshold"] == original.threshold
        assert store.current is replacement
        subsequent = http.post("/predict", json=payload)
        assert subsequent.status_code == 200, subsequent.text
        assert subsequent.json()["model_version"] == "replacement"
        assert subsequent.json()["threshold"] == 1.0
    finally:
        store.swap(original)


@pytest.mark.parametrize(
    "delta,recommendation", [(-0.001, "approve"), (0.0, "review"), (0.001, "review")]
)
def test_legacy_prediction_wire_contract_and_threshold_boundary(
    client, monkeypatch, delta, recommendation
):
    import numpy as np

    import risklab.api.service as service

    http, _ = client
    threshold = http.app.state.store.current.threshold
    monkeypatch.setattr(
        service, "predict_proba", lambda model, frame: np.array([threshold + delta])
    )
    response = http.post(
        "/predict",
        json={
            "request_id": "synthetic-contract-check",
            "application": {"AMT_CREDIT": 100000, "AMT_INCOME_TOTAL": 100000},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "request_id",
        "model_version",
        "contract_version",
        "probability",
        "threshold",
        "recommendation",
        "sources_used",
        "warnings",
        "explanation",
    }
    assert body["request_id"] == response.headers["x-request-id"] == "synthetic-contract-check"
    assert body["contract_version"] == "1.1"
    assert body["recommendation"] == recommendation
    assert body["probability"] == threshold + delta
    assert body["explanation"] is None
