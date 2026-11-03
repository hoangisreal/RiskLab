"""Full control engine freezes calibration before a separately seeded validation measurement."""

import json

import numpy as np
import pandas as pd
import pytest

from risklab.api.model_store import LoadedModel
from risklab.config import load_model
from risklab.monitoring import policy
from risklab.monitoring.profile import make_profile
from risklab.monitoring.service import MonitoringConfig

pytestmark = pytest.mark.integration


def test_controls_use_one_engine_and_separate_registered_seed_ranges(
    app_env, mlflow_server, monkeypatch
):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", mlflow_server)
    cfg = load_model(app_env.config_dir, "monitoring.yaml", MonitoringConfig)
    cfg = cfg.model_copy(
        update={
            "batch_size": 200,
            "calibration_control_batches": 20,
            "validation_control_batches": 20,
            "performance_bootstrap_resamples": 100,
            "performance_min_auc": 0.0,
            "performance_max_log_loss": 5.0,
            "performance_auc_drop": 0.2,
            "performance_log_loss_increase": 0.2,
            "performance_brier_increase": 0.2,
        }
    )
    rng = np.random.default_rng(4)

    class Model:
        feature_names_in_ = np.array(["x"])

        def predict_proba(self, frame):
            p = 1 / (1 + np.exp(-frame["x"].to_numpy()))
            return np.column_stack([1 - p, p])

    model = Model()
    train = pd.DataFrame({"x": rng.normal(size=2000)})
    selection = pd.DataFrame({"x": rng.normal(size=2000)})
    scores = model.predict_proba(selection)[:, 1]
    profile = make_profile(
        train,
        scores,
        model_sha256="a" * 64,
        contract_version="1.1",
        bins=cfg.reference_bins,
        cdf_points=cfg.reference_cdf_points,
        selection_labels=(rng.random(len(scores)) < scores).astype(int),
    )
    pool = pd.DataFrame({"x": rng.normal(size=2000), "AMT_CREDIT": np.full(2000, 100.0)})
    labels = pd.Series((rng.random(len(pool)) < model.predict_proba(pool)[:, 1]).astype(int))
    monkeypatch.setattr(policy, "load_monitoring_pool", lambda *a, **kw: (pool, labels))
    loaded = LoadedModel("synthetic-controls", model, 0.1, "1.1", [], run_id="synthetic-model")
    result = policy.calibrate_controls(
        loaded,
        profile,
        config=cfg,
        config_dir=app_env.config_dir,
        root=app_env.config_dir.parent,
        reports_dir=app_env.data_cfg.reports_dir,
    )
    assert result["passed"]
    assert result["scale"] in cfg.alert_scale_candidates
    assert result["run_id"]
    assert result["validation_false_alarm_rate"] <= cfg.false_alarm_target
    _, run_id = policy.effective_config(cfg, profile, reports_dir=app_env.data_cfg.reports_dir)
    assert run_id == result["run_id"]
    manifest = json.loads((app_env.config_dir.parent / "data/monitoring/manifest.json").read_text())
    assert len(manifest["batches"]) == 40
    assert len({row["seed"] for row in manifest["batches"]}) == 40
