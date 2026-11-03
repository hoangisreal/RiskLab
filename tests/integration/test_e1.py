from __future__ import annotations

import json
from pathlib import Path

import mlflow
import pyarrow.parquet as pq
import pytest
import yaml

from risklab.config import ConfigError
from risklab.experiments.e1 import (
    LGBM_NAME,
    LR_NAME,
    Decision,
    decide,
    load_e1_config,
    run_e1,
)
from risklab.models.evaluate import compute_metrics
from risklab.tracking import TrackingError
from tests.conftest import CONFIG_DIR
from tests.integration.conftest import AppEnv

pytestmark = pytest.mark.integration

SMALL_E1 = {
    "experiment_name": "test-e1",
    "feature_set": "application",
    "seed": 3,
    "inner_cv": {"folds": 3, "seed": 3},
    "lr": {"c_grid": [0.1, 1.0]},
    "lgbm": {"max_rounds": 40, "early_stopping_rounds": 10},
    "decision": {
        "primary_metric": "roc_auc",
        "min_gain": 0.005,
        "bootstrap_resamples": 100,
        "bootstrap_seed": 3,
        "ci_level": 0.95,
    },
}


@pytest.fixture
def e1_env(app_env: AppEnv) -> AppEnv:
    target = app_env.config_dir / "experiments"
    target.mkdir(exist_ok=True)
    (target / "e1.yaml").write_text(yaml.safe_dump(SMALL_E1))
    return app_env


def _run(env: AppEnv, out: Path, *, tracking: bool) -> dict:
    return run_e1(
        config_dir=env.config_dir,
        root=env.config_dir.parent,
        output_dir=out,
        data_cfg=env.data_cfg,
        partition_cfg=env.partition_cfg,
        tracking=tracking,
    )


def test_shipped_e1_config_is_valid_and_declares_the_rule() -> None:
    cfg = load_e1_config(CONFIG_DIR)
    assert cfg.decision.primary_metric == "roc_auc"
    assert cfg.decision.min_gain > 0
    assert cfg.decision.bootstrap_resamples >= 100


def test_invalid_e1_config_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "experiments").mkdir()
    bad = {**SMALL_E1, "lr": {"c_grid": [0.1, 0.1]}}
    (tmp_path / "experiments" / "e1.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError):
        load_e1_config(tmp_path)
    bad2 = {**SMALL_E1, "surprise": 1}
    (tmp_path / "experiments" / "e1.yaml").write_text(yaml.safe_dump(bad2))
    with pytest.raises(ConfigError):
        load_e1_config(tmp_path)


@pytest.mark.parametrize(
    ("delta", "lo", "hi", "metric", "expected"),
    [
        (0.010, 0.004, 0.016, "roc_auc", LGBM_NAME),  # gain ok, CI excludes 0
        (0.010, -0.001, 0.021, "roc_auc", LR_NAME),  # CI includes 0
        (0.004, 0.001, 0.007, "roc_auc", LR_NAME),  # significant but below min_gain
        (0.005, 0.001, 0.009, "roc_auc", LGBM_NAME),  # exactly min_gain
        (-0.02, -0.03, -0.01, "roc_auc", LR_NAME),
        (-0.010, -0.016, -0.004, "brier", LGBM_NAME),  # lower is better: improvement
        (0.010, 0.004, 0.016, "brier", LR_NAME),  # lower is better: worse
    ],
)
def test_decision_rule(delta: float, lo: float, hi: float, metric: str, expected: str) -> None:
    rule = Decision(
        primary_metric=metric,  # type: ignore[arg-type]
        min_gain=0.005,
        bootstrap_resamples=100,
        bootstrap_seed=1,
        ci_level=0.95,
    )
    out = decide({"delta": delta, "ci_low": lo, "ci_high": hi}, rule)
    assert out["preferred"] == expected


def test_run_without_tracking_writes_reproducible_outputs(e1_env: AppEnv, tmp_path: Path) -> None:
    s1 = _run(e1_env, tmp_path / "a", tracking=False)
    s2 = _run(e1_env, tmp_path / "b", tracking=False)
    assert s1["selection_metrics"] == s2["selection_metrics"]
    assert s1["paired_bootstrap_lgbm_minus_lr"] == s2["paired_bootstrap_lgbm_minus_lr"]
    assert s1["decision"] == s2["decision"]
    assert s1["run_ids"] == {}
    assert s1["inner_cv"][LR_NAME]["best_c"] in (0.1, 1.0)
    assert 1 <= s1["inner_cv"][LGBM_NAME]["chosen_rounds"] <= 40

    # The prediction file reproduces the reported metrics without any RiskLab model code.
    table = pq.read_table(tmp_path / "a" / "selection_predictions.parquet").to_pandas()
    assert len(table) == s1["selection_rows"]
    for model in (LR_NAME, LGBM_NAME):
        recomputed = compute_metrics(table["TARGET"], table[model])
        assert recomputed == pytest.approx(s1["selection_metrics"][model])
    summary_file = json.loads((tmp_path / "a" / "summary.json").read_text())
    assert summary_file["decision"] == s1["decision"]


def test_selection_is_evaluated_not_train_or_test(e1_env: AppEnv, tmp_path: Path) -> None:
    from risklab.data.partition import read_role_ids

    s = _run(e1_env, tmp_path / "o", tracking=False)
    preds = pq.read_table(tmp_path / "o" / "selection_predictions.parquet")
    ids = set(preds["SK_ID_CURR"].to_pylist())
    out = e1_env.partition_cfg.output_dir
    assert ids == set(read_role_ids(out / "selection.parquet", "SK_ID_CURR"))
    assert not ids & set(read_role_ids(out / "test.parquet", "SK_ID_CURR"))
    assert not ids & set(read_role_ids(out / "train.parquet", "SK_ID_CURR"))
    assert s["selection_rows"] == len(ids)


def test_tracking_requires_a_server_uri(
    e1_env: AppEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    with pytest.raises(TrackingError):
        _run(e1_env, tmp_path / "o", tracking=True)


def test_tracked_run_logs_provenance_metrics_and_artifacts(
    e1_env: AppEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mlflow_server
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", mlflow_server)
    summary = _run(e1_env, tmp_path / "o", tracking=True)
    ids = summary["run_ids"]
    assert set(ids) == {LR_NAME, LGBM_NAME, "comparison"}
    client = mlflow.MlflowClient()
    lr = client.get_run(ids[LR_NAME])
    assert lr.data.tags["partition_hash"] == summary["partition_hash"]
    assert lr.data.tags["experiment_id"] == "E1"
    assert "config_hash" in lr.data.tags and "git_commit" in lr.data.tags
    assert lr.data.metrics["selection_roc_auc"] == pytest.approx(
        summary["selection_metrics"][LR_NAME]["roc_auc"]
    )
    arts = {a.path for a in client.list_artifacts(ids[LGBM_NAME])}
    assert {"selection_predictions.parquet", "selection_curves.png"} <= arts
    comp = client.get_run(ids["comparison"])
    assert comp.data.tags["preferred_model"] == summary["decision"]["preferred"]
    assert "delta_roc_auc_ci_low" in comp.data.metrics
