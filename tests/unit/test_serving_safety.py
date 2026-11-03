"""Serving must fail closed and reload must change the actual pinned model."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from risklab.api.main import create_app
from risklab.api.model_store import LoadedModel, ModelStore, load_from_registry
from risklab.cli import app
from risklab.manifest import library_versions
from risklab.models.select import protocol_hashes
from risklab.registry.bundle import ServingConfig
from tests.conftest import CONFIG_DIR, REPO_ROOT

pytestmark = pytest.mark.unit


@pytest.fixture
def bundle():
    digest = "a" * 64
    return ServingConfig(
        threshold=0.0839,
        calibration_method="none",
        contract_version="1.1",
        feature_groups=[],
        feature_columns=["AMT_CREDIT"],
        lock_id="b" * 16,
        model_sha256=digest,
        reference_profile_sha256="c" * 64,
        partition_hash="partition",
        data_manifest_hash="manifest",
        git_commit="commit",
        protocol_hashes=protocol_hashes(REPO_ROOT),
        library_versions=library_versions(),
        selection_metrics={"roc_auc": 0.75, "pr_auc": 0.25, "brier": 0.07, "log_loss": 0.25},
        cost={"cost_fp": 1.0, "cost_fn": 10.0},
        selection_ece=0.01,
        selection_run_id="selection-run",
        parity={
            "passed": True,
            "model_sha256": digest,
            "feature_columns": ["AMT_CREDIT"],
            "rows": 2,
        },
    )


def test_reload_changes_current_and_failed_reload_keeps_it(monkeypatch, bundle):
    import mlflow.sklearn

    from risklab.registry import client

    old = LoadedModel("1", object(), 0.1, "1.1", [])
    store = ModelStore()
    store.swap(old)
    http = TestClient(create_app(store=store, root=REPO_ROOT, config_dir=CONFIG_DIR))
    monkeypatch.setenv("RISKLAB_ADMIN_API_KEY", "synthetic-key")
    monkeypatch.setattr(client, "resolve_champion", lambda: {"version": "2", "run_id": "new-run"})
    monkeypatch.setattr(client, "read_serving_config", lambda info: bundle)
    monkeypatch.setattr(client, "read_reference_profile", lambda *args: None)
    model = SimpleNamespace(feature_names_in_=np.array(["AMT_CREDIT"]))
    loader = Mock(return_value=model)
    monkeypatch.setattr(mlflow.sklearn, "load_model", loader)
    assert http.post("/admin/models/reload").status_code == 403
    response = http.post("/admin/models/reload", headers={"x-admin-key": "synthetic-key"})
    assert response.status_code == 200
    assert http.get("/models/current").json()["model_version"] == "2"
    assert store.current.model is model
    assert store.current.threshold == bundle.threshold
    loader.assert_called_once_with("models:/risklab-credit-risk/2")
    loader.side_effect = ValueError("corrupt artifact")
    assert (
        http.post("/admin/models/reload", headers={"x-admin-key": "synthetic-key"}).status_code
        == 502
    )
    assert store.current.model is model


@pytest.mark.parametrize("change", ["contract", "versions", "columns", "parity", "groups", "label"])
def test_registry_load_rejects_bad_bundle_without_swap(monkeypatch, bundle, change):
    import mlflow.sklearn

    from risklab.registry import client

    if change == "contract":
        bundle.contract_version = "9.9"
    elif change == "versions":
        bundle.library_versions = {**bundle.library_versions, "lightgbm": "wrong"}
    elif change == "parity":
        bundle.parity = {**bundle.parity, "model_sha256": "wrong"}
    elif change == "groups":
        bundle.feature_groups = ["unimplemented"]
    elif change == "label":
        bundle.feature_columns = ["TARGET"]
        bundle.parity = {**bundle.parity, "feature_columns": ["TARGET"]}
    columns = ["wrong"] if change == "columns" else list(bundle.feature_columns)
    monkeypatch.setattr(client, "resolve_champion", lambda: {"version": "2", "run_id": "new-run"})
    monkeypatch.setattr(client, "read_serving_config", lambda info: bundle)
    monkeypatch.setattr(client, "read_reference_profile", lambda *args: None)
    monkeypatch.setattr(
        mlflow.sklearn, "load_model", lambda uri: SimpleNamespace(feature_names_in_=columns)
    )
    store = ModelStore()
    old = LoadedModel("1", object(), 0.1, "1.1", [])
    store.swap(old)
    with pytest.raises(ValueError):
        load_from_registry(store)
    assert store.current is old


def test_promote_requires_gate_and_force_reason(monkeypatch, tmp_path):
    import risklab.cli as cli
    from risklab.registry import client

    alias = Mock()
    monkeypatch.setattr(
        client, "check_promotion", lambda *a, **kw: {"passed": False, "checks": {"auc": False}}
    )
    monkeypatch.setattr(client, "champion_info_or_none", lambda: {"version": "1"})
    monkeypatch.setattr(client, "set_alias", alias)
    monkeypatch.setattr(cli, "_audit_path", lambda ctx: tmp_path / "audit.jsonl")
    runner = CliRunner()
    for args in ([], ["--force"], ["--force", "--reason", "  "]):
        result = runner.invoke(app, ["registry", "promote", "--version", "2", *args])
        assert result.exit_code == 1, result.output
        alias.assert_not_called()
    result = runner.invoke(
        app, ["registry", "promote", "--version", "2", "--force", "--reason", "regression demo"]
    )
    assert result.exit_code == 0, result.output
    alias.assert_called_once_with("2")
    assert "FORCE: regression demo" in (tmp_path / "audit.jsonl").read_text()


def test_diagnostic_model_card_uses_its_own_identity_and_metrics(tmp_path, bundle):
    from risklab.registry.register import _model_card_text

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/model_card.md").write_text("selected-model-description")
    assert "selected-model-description" in _model_card_text(tmp_path, bundle)
    bundle.purpose = "rollback_demo_only"
    text = _model_card_text(tmp_path, bundle)
    assert "selected-model-description" not in text
    assert bundle.selection_run_id in text and bundle.model_sha256 in text
    assert "roc_auc: 0.75" in text
    assert "No Test evaluation is claimed" in text
