"""Exercise all model columns and predictions, including a deliberately corrupted SQL output."""

import pytest

from risklab.features.application import FeatureColumns
from risklab.features.historical import combined_columns, load_full_frame
from risklab.features.parity import verify_parity
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline

pytestmark = pytest.mark.integration


def test_all_feature_and_prediction_parity(app_env, monkeypatch):
    cfg = app_env.config_dir
    groups = ["previous", "installments", "bureau", "bureau_balance", "pos_cash", "credit_card"]
    frame, labels = load_full_frame("train", "fit", groups, config_dir=cfg, root=cfg.parent)
    numeric, categorical = combined_columns(groups, config_dir=cfg)
    columns = FeatureColumns(numeric=numeric, categorical=categorical, log=[])
    params = load_lgbm_params(cfg).model_copy(update={"n_estimators": 10})
    model = fit_pipeline(
        make_lgbm_pipeline(params, columns=columns, config_dir=str(cfg)), frame, labels
    )
    report = verify_parity(
        model, groups, config_dir=cfg, root=cfg.parent, sample_size=5, seed=17, rtol=1e-9
    )
    assert report["passed"], report
    assert set(report["feature_columns"]) == set(frame.columns)
    import risklab.features.parity as parity

    original = parity.prepare_input

    def corrupt(*args, **kwargs):
        online, warnings = original(*args, **kwargs)
        online["inst_late_ratio"] = 99.0
        return online, warnings

    monkeypatch.setattr(parity, "prepare_input", corrupt)
    report = verify_parity(
        model, groups, config_dir=cfg, root=cfg.parent, sample_size=2, seed=17, rtol=1e-9
    )
    assert not report["passed"]
    assert all("inst_late_ratio" in f["columns"] for f in report["failures"])
