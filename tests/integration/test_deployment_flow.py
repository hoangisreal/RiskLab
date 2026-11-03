"""Synthetic HTTP registry: E2 -> E3 artifact -> threshold -> parity -> lock -> serve, no Test."""

import json
import re

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from risklab.api.main import create_app
from risklab.api.model_store import ModelStore
from risklab.cli import app
from risklab.models import select

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_selected_artifact_registers_and_serves_without_refit_or_test(
    app_env, mlflow_server, monkeypatch
):
    cfg = app_env.config_dir
    monkeypatch.setenv("MLFLOW_TRACKING_URI", mlflow_server)
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    # Small, predetermined synthetic budgets; production configs remain untouched.
    changes = {
        "model_lgbm.yaml": {"n_estimators": 10},
        "experiments/e2.yaml": {"bootstrap_resamples": 100},
        "experiments/e1_served.yaml": {
            "inner_cv": {"folds": 2, "seed": 11},
            "lr": {"c_grid": [0.1]},
            "lgbm": {"max_rounds": 10, "early_stopping_rounds": 3},
        },
        "experiments/e3.yaml": {
            "bootstrap_resamples": 100,
            "include_scale_pos_weight_diagnostic": False,
        },
        "calibration.yaml": {"bootstrap_resamples": 100},
        "serving_validation.yaml": {"parity_sample_size": 3},
        "registry.yaml": {"floor_roc_auc": 0.0, "floor_log_loss": 10.0, "ece_limit": 1.0},
    }
    for name, update in changes.items():
        path = cfg / name
        document = yaml.safe_load(path.read_text())
        path.write_text(yaml.safe_dump({**document, **update}))
    runner = CliRunner()
    prefix = ["--config-dir", str(cfg)]

    def run(*args):
        result = runner.invoke(app, [*prefix, *args])
        assert result.exit_code == 0, result.output
        return result.output

    from risklab.models import final_eval

    monkeypatch.setattr(final_eval, "_load_test_frame", lambda *a, **kw: pytest.fail("Test opened"))
    run("e2")
    e2 = json.loads((app_env.data_cfg.reports_dir / "experiments/e2/summary.json").read_text())
    assert len(e2["leave_one_group_out"]) == 6
    run("e1", "--served")
    import risklab.experiments.e234 as experiments

    monkeypatch.setattr(experiments, "fit_pipeline", lambda *a, **kw: pytest.fail("refit after E1"))
    run("e3")
    run("e4")
    run("prepare-candidate")
    monkeypatch.setattr(
        select, "git_info", lambda cwd=None: {"commit": "synthetic-clean-commit", "dirty": False}
    )
    candidate_path = app_env.data_cfg.reports_dir / "models/candidate.json"
    valid_candidate = candidate_path.read_text()
    for key, value in (
        ("protocol_hashes", {}),
        ("library_versions", {}),
        ("contract_version", "stale"),
    ):
        changed = json.loads(valid_candidate)
        changed[key] = value
        candidate_path.write_text(json.dumps(changed))
        rejected = runner.invoke(
            app, [*prefix, "lock-selection", "--candidate", str(candidate_path)]
        )
        assert rejected.exit_code == 1
        assert "context changed" in rejected.output
    candidate_path.write_text(valid_candidate)
    output = run(
        "lock-selection", "--candidate", str(app_env.data_cfg.reports_dir / "models/candidate.json")
    )
    lock_id = re.search(r"Selection locked: ([0-9a-f]+)", output).group(1)
    output = run("registry", "register", "--lock", lock_id)
    version = re.search(r"Registered version (\d+)", output).group(1)
    run("registry", "promote", "--version", version, "--reason", "synthetic deployment test")
    store = ModelStore()
    http = TestClient(create_app(store=store, config_dir=cfg, root=cfg.parent))
    assert http.get("/health").json()["ready"]
    assert http.get("/models/current").json()["lock_id"] == lock_id
    candidate = json.loads((app_env.data_cfg.reports_dir / "models/candidate.json").read_text())
    assert store.current.threshold == candidate["threshold"]
    run("registry", "rollback", "--version", version, "--reason", "synthetic rollback validation")
    monkeypatch.setenv("RISKLAB_ADMIN_API_KEY", "synthetic-admin-key")
    reloaded = http.post("/admin/models/reload", headers={"x-admin-key": "synthetic-admin-key"})
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["version"] == version
    # Compare the registered/reloaded model with the exact selected local artifact.
    import pickle

    from risklab.features.historical import load_full_frame

    with open(candidate["model_path"], "rb") as fh:
        original = pickle.load(fh)
    frame, _ = load_full_frame("selection", "select", candidate["feature_groups"], config_dir=cfg)
    columns = candidate["feature_columns"]
    np.testing.assert_allclose(
        store.current.model.predict_proba(frame[columns]),
        original.predict_proba(frame[columns]),
        rtol=0,
        atol=0,
    )
    from risklab.features.payload import train_payloads

    training, _ = load_full_frame("train", "fit", candidate["feature_groups"], config_dir=cfg)
    for applicant, payload in train_payloads(
        applicant_ids=training.index[:2].tolist(),
        groups=candidate["feature_groups"],
        config_dir=cfg,
        root=cfg.parent,
    ):
        response = http.post("/predict", json=payload)
        assert response.status_code == 200, response.text
        expected = original.predict_proba(training.loc[[applicant], columns])[0, 1]
        assert response.json()["probability"] == expected
    assert not (app_env.data_cfg.reports_dir / "final_eval/ledger.jsonl").exists()
