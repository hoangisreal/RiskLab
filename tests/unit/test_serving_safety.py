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
    from risklab.registry import client

    old = LoadedModel("1", object(), 0.1, "1.1", [])
    store = ModelStore()
    store.swap(old)
    http = TestClient(create_app(store=store, root=REPO_ROOT, config_dir=CONFIG_DIR))
    monkeypatch.setenv("RISKLAB_ADMIN_API_KEY", "synthetic-key")
    monkeypatch.setattr(
        client,
        "resolve_champion",
        lambda: {"version": "2", "run_id": "new-run", "registered_at_ms": 1790000000000},
    )
    monkeypatch.setattr(client, "read_serving_config", lambda info: bundle)
    monkeypatch.setattr(client, "read_reference_profile", lambda *args: None)
    model = SimpleNamespace(feature_names_in_=np.array(["AMT_CREDIT"]))
    loader = Mock(return_value=model)
    monkeypatch.setattr(client, "read_evaluated_model", loader)
    assert http.post("/admin/models/reload").status_code == 403
    response = http.post("/admin/models/reload", headers={"x-admin-key": "synthetic-key"})
    assert response.status_code == 200
    assert http.get("/models/current").json()["model_version"] == "2"
    assert http.get("/models/current").json()["registered_at_ms"] == 1790000000000
    assert store.current.model is model
    assert store.current.threshold == bundle.threshold
    assert loader.call_args.args[0]["version"] == "2"
    assert loader.call_args.args[1] is bundle
    loader.side_effect = ValueError("corrupt artifact")
    assert (
        http.post("/admin/models/reload", headers={"x-admin-key": "synthetic-key"}).status_code
        == 502
    )
    assert store.current.model is model


@pytest.mark.parametrize("change", ["contract", "versions", "columns", "parity", "groups", "label"])
def test_registry_load_rejects_bad_bundle_without_swap(monkeypatch, bundle, change):
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
        client,
        "read_evaluated_model",
        lambda info, config: SimpleNamespace(feature_names_in_=columns),
    )
    store = ModelStore()
    old = LoadedModel("1", object(), 0.1, "1.1", [])
    store.swap(old)
    with pytest.raises(ValueError):
        load_from_registry(store)
    assert store.current is old


def test_protocol_mismatch_is_rejected_before_artifact_loading(monkeypatch, bundle):
    from risklab.api.model_store import ModelStoreError
    from risklab.registry import client

    monkeypatch.setattr(client, "resolve_champion", lambda: {"version": "2", "run_id": "new-run"})
    monkeypatch.setattr(client, "read_serving_config", lambda info: bundle)
    profile_reader = Mock()
    model_loader = Mock()
    monkeypatch.setattr(client, "read_reference_profile", profile_reader)
    monkeypatch.setattr(client, "read_evaluated_model", model_loader)
    store = ModelStore()
    old = LoadedModel("1", object(), 0.1, "1.1", [])
    store.swap(old)
    with pytest.raises(ModelStoreError, match="differs from this serving image"):
        load_from_registry(store, expected_protocol={"changed-source": "a" * 64})
    assert store.current is old
    profile_reader.assert_not_called()
    model_loader.assert_not_called()


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


@pytest.mark.parametrize(
    "fault", [None, "report_lock", "artifact", "journal_run", "missing_run", "metric", "partial"]
)
def test_current_metadata_requires_matching_completed_evaluation(
    tmp_path, monkeypatch, bundle, fault
):
    import json

    import risklab.api.main as api_main
    from risklab.manifest import sha256_file
    from risklab.models import select

    reports = tmp_path / "reports"
    model = tmp_path / "frozen-model.bin"
    model.write_bytes(b"synthetic-frozen-artifact")
    monkeypatch.setattr(select, "git_info", lambda _: {"commit": "synthetic", "dirty": False})
    lock = select.create_lock(
        locks_dir=reports / "locks",
        model_run_id=bundle.selection_run_id,
        feature_set="application",
        feature_groups=bundle.feature_groups,
        contract_version=bundle.contract_version,
        calibration_method=bundle.calibration_method,
        threshold=bundle.threshold,
        config={},
        partition_hash=bundle.partition_hash,
        data_manifest_hash=bundle.data_manifest_hash,
        model_path=model,
        root=REPO_ROOT,
    )
    bundle.lock_id = lock["lock_id"]
    bundle.model_sha256 = sha256_file(model)
    result = {
        "lock_id": lock["lock_id"],
        "model_run_id": bundle.selection_run_id,
        "run_id": "synthetic-final-run",
        "metrics": dict(bundle.selection_metrics),
        "metric_ci": {
            name: {"ci_low": value - 0.01, "ci_high": value + 0.01}
            for name, value in bundle.selection_metrics.items()
        },
        "ci_level": 0.95,
        "rows": 1000,
        "test_openings_total": 1,
        "evaluated_at": "2026-11-01T00:00:00+00:00",
        "threshold": bundle.threshold,
        "calibration_method": bundle.calibration_method,
        "predictions_path": "private-row-predictions",
    }
    entry = {key: result[key] for key in ("lock_id", "run_id", "test_openings_total")}
    if fault == "report_lock":
        result["lock_id"] = "c" * 16
    elif fault == "artifact":
        bundle.model_sha256 = "d" * 64
    elif fault == "journal_run":
        entry["run_id"] = "another-run"
    elif fault == "missing_run":
        result["run_id"] = None
    elif fault == "metric":
        result["metrics"]["roc_auc"] = float("nan")
    directory = reports / "final_eval" / lock["lock_id"]
    directory.mkdir(parents=True)
    (directory / "result.json").write_text("{" if fault == "partial" else json.dumps(result))
    (directory.parent / "ledger.jsonl").write_text(json.dumps(entry) + "\n")
    monkeypatch.setattr(
        api_main, "load_data_config", lambda _: SimpleNamespace(reports_dir=reports)
    )
    store = ModelStore()
    store.swap(LoadedModel("2", object(), bundle.threshold, "1.1", [], serving_config=bundle))
    http = TestClient(create_app(store=store, root=REPO_ROOT, config_dir=CONFIG_DIR))
    response = http.get("/models/current")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["feature_columns"] == ["AMT_CREDIT"]
    assert len(metadata["feature_schema_hash"]) == 64
    assert metadata["protocol_hashes"] == bundle.protocol_hashes
    assert metadata["library_versions"] == bundle.library_versions
    if fault is None:
        final = metadata["final_evaluation"]
        assert final["metrics"] == result["metrics"]
        assert final["run_id"] == result["run_id"]
        assert final["lock_id"] == lock["lock_id"]
        assert "predictions_path" not in final
    else:
        assert metadata["final_evaluation"] is None
    (directory / "result.json").unlink()
    assert http.get("/models/current").json()["final_evaluation"] is None


def test_evaluated_artifact_hash_checked_before_loading(tmp_path, monkeypatch, bundle):
    import hashlib
    import pickle

    import mlflow.artifacts

    from risklab.registry.client import RegistryError, read_evaluated_model

    artifact = tmp_path / "model.pkl"
    payload = pickle.dumps(SimpleNamespace(feature_names_in_=["AMT_CREDIT"], weight=1))
    artifact.write_bytes(payload)
    bundle.model_sha256 = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(mlflow.artifacts, "download_artifacts", lambda **kwargs: str(tmp_path))
    assert read_evaluated_model({"run_id": "synthetic"}, bundle).weight == 1
    artifact.write_bytes(pickle.dumps(SimpleNamespace(feature_names_in_=["AMT_CREDIT"], weight=2)))
    monkeypatch.setattr(pickle, "loads", lambda _: pytest.fail("unverified bytes loaded"))
    with pytest.raises(RegistryError, match="hash differs"):
        read_evaluated_model({"run_id": "synthetic"}, bundle)
