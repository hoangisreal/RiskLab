"""Real-data smoke for the application frame and pipelines.

Deliberately computes no performance metric: scoring Selection here only confirms shapes and
finiteness. Metrics belong to the pre-declared E1 experiment.
"""

from __future__ import annotations

import resource
import time

import numpy as np
import pytest

from risklab.config import load_data_config, load_partition_config
from risklab.data.loaders import PartitionRole, Purpose
from risklab.features.application import load_application_frame, load_features_config
from risklab.models.pipelines import (
    load_lgbm_params,
    load_lr_params,
    make_lgbm_pipeline,
    make_lr_pipeline,
    output_feature_names,
)
from tests.conftest import CONFIG_DIR

pytestmark = [pytest.mark.data, pytest.mark.slow]


def test_real_frames_and_pipelines_fit_and_score_shapes(capsys: pytest.CaptureFixture[str]) -> None:
    data_cfg = load_data_config(CONFIG_DIR)
    part_cfg = load_partition_config(CONFIG_DIR)
    if (
        not (part_cfg.output_dir / "partitions.json").is_file()
        or not (data_cfg.parquet_dir / "application_train.parquet").is_file()
    ):
        pytest.skip("ingested and partitioned real data is absent")

    X, y = load_application_frame(PartitionRole.train, Purpose.fit, config_dir=CONFIG_DIR)
    Xs, ys = load_application_frame(PartitionRole.selection, Purpose.select, config_dir=CONFIG_DIR)
    assert list(X.columns) == list(Xs.columns)
    assert X.index.equals(y.index) and Xs.index.equals(ys.index)
    assert not set(X.index) & set(Xs.index)

    cfg = load_features_config(CONFIG_DIR)
    report = [f"train X={X.shape} selection X={Xs.shape}"]
    lgbm_params = load_lgbm_params(CONFIG_DIR).model_copy(update={"n_estimators": 50})
    for name, pipe in (
        ("lr", make_lr_pipeline(load_lr_params(CONFIG_DIR), cfg, config_dir=CONFIG_DIR)),
        ("lgbm", make_lgbm_pipeline(lgbm_params, cfg, config_dir=CONFIG_DIR)),
    ):
        t0 = time.perf_counter()
        pipe.fit(X, y)
        fit_s = time.perf_counter() - t0
        proba = pipe.predict_proba(Xs)
        assert proba.shape == (len(Xs), 2)
        assert np.isfinite(proba).all()
        assert ((proba >= 0) & (proba <= 1)).all()
        report.append(f"{name}: output features={len(output_feature_names(pipe))} fit={fit_s:.1f}s")
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    report.append(f"peak RSS={peak_mb:.0f} MB")
    with capsys.disabled():
        print("\n" + "\n".join(report))
