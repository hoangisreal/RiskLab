"""Selection lock + final-eval gate on synthetic data (the only Test reader)."""

from __future__ import annotations

import pickle

import pytest

from risklab.features.application import load_application_frame
from risklab.models import final_eval as fe
from risklab.models import select as sel
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline

pytestmark = pytest.mark.integration


def test_lock_and_single_open(app_env, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
        X.head(800),
        y.head(800),
    )
    model_path = tmp_path / "model.pkl"
    with open(model_path, "wb") as fh:
        pickle.dump(pipe, fh)
    clean = {"commit": "abc123", "dirty": False}
    monkeypatch.setattr(sel, "git_info", lambda cwd=None: clean)
    monkeypatch.setattr(fe, "verify_lock", lambda lock, *, root: None)
    # verify_lock is imported into final_eval's namespace at call time via select;
    # patch the select module function used through the lock dict instead.
    lock = sel.create_lock(
        locks_dir=tmp_path / "locks",
        model_run_id="run-1",
        feature_set="application",
        feature_groups=[],
        contract_version="1.0",
        calibration_method="none",
        threshold=0.1,
        config={"a": 1},
        partition_hash="ph",
        data_manifest_hash="mh",
        model_path=model_path,
        root=tmp_path,
    )
    reports = tmp_path / "reports"
    kwargs = dict(
        lock_id=lock["lock_id"],
        locks_dir=tmp_path / "locks",
        reports_dir=reports,
        root=tmp_path,
        data_cfg=app_env.data_cfg,
        partition_cfg=app_env.partition_cfg,
        config_dir=cfg_dir,
    )
    first = fe.run_final_eval(**kwargs)  # type: ignore[arg-type]
    assert 0.0 <= first["metrics"]["roc_auc"] <= 1.0
    assert first["test_openings_total"] == 1
    again = fe.run_final_eval(**kwargs)  # type: ignore[arg-type]
    assert again["lock_id"] == first["lock_id"]  # idempotent: no second opening
    entries = fe._ledger_entries(reports)
    assert len(entries) == 1
