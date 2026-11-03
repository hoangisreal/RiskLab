"""Selection lock + final-eval gate on synthetic data (the only Test reader)."""

from __future__ import annotations

import pickle

import pytest

from risklab.data.loaders import partition_info
from risklab.features.application import load_application_frame
from risklab.manifest import sha256_file
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
    lock = sel.create_lock(
        locks_dir=tmp_path / "locks",
        model_run_id="run-1",
        feature_set="application",
        feature_groups=[],
        contract_version="1.1",
        calibration_method="none",
        threshold=0.1,
        config={"evaluation": {"bootstrap_resamples": 100}},
        partition_hash=partition_info("train", config_dir=cfg_dir).partition_hash,
        data_manifest_hash=sha256_file(app_env.data_cfg.manifest_path),
        model_path=model_path,
        root=cfg_dir.parent,
    )
    reports = tmp_path / "reports"
    kwargs = dict(
        lock_id=lock["lock_id"],
        locks_dir=tmp_path / "locks",
        reports_dir=reports,
        root=cfg_dir.parent,
        data_cfg=app_env.data_cfg,
        partition_cfg=app_env.partition_cfg,
        config_dir=cfg_dir,
    )
    first = fe.run_final_eval(**kwargs)  # type: ignore[arg-type]
    assert 0.0 <= first["metrics"]["roc_auc"] <= 1.0
    assert first["test_openings_total"] == 1
    assert set(first["metric_ci"]) == set(first["metrics"])
    assert first["subgroups"]
    assert "has_bureau_history" in {row["group"] for row in first["subgroups"]}
    assert first["calibration"]["ece"] >= 0
    assert (reports / "manifests/final-eval.json").is_file()
    assert (reports / "final_eval" / lock["lock_id"] / "predictions.parquet").is_file()
    again = fe.run_final_eval(**kwargs)  # type: ignore[arg-type]
    assert again["lock_id"] == first["lock_id"]  # idempotent: no second opening
    entries = fe._ledger_entries(reports)
    assert len(entries) == 1

    other = sel.create_lock(
        locks_dir=tmp_path / "locks",
        model_run_id="run-1",
        feature_set="application",
        feature_groups=[],
        contract_version="1.1",
        calibration_method="none",
        threshold=0.2,
        config={"evaluation": {"bootstrap_resamples": 100}},
        partition_hash=partition_info("train", config_dir=cfg_dir).partition_hash,
        data_manifest_hash=sha256_file(app_env.data_cfg.manifest_path),
        model_path=model_path,
        root=cfg_dir.parent,
    )
    for acknowledgment in (None, "", "  "):
        with pytest.raises(fe.FinalEvalError, match="already been opened"):
            fe.run_final_eval(
                **{**kwargs, "lock_id": other["lock_id"], "acknowledge_test_reuse": acknowledgment}
            )

    # A failed attempt remains recorded, so a crash cannot silently reopen Test.
    def fail_read(*args, **kw):
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(fe, "_load_test_frame", fail_read)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        fe.run_final_eval(
            **{
                **kwargs,
                "lock_id": other["lock_id"],
                "acknowledge_test_reuse": "synthetic reuse test",
            }
        )
    assert len(fe._ledger_entries(reports)) == 2
    with pytest.raises(fe.FinalEvalError, match="already been opened"):
        fe.run_final_eval(**{**kwargs, "lock_id": other["lock_id"]})
