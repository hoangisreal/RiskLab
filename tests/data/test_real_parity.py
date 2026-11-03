"""Complete batch/API feature and prediction parity on 500 Train applicants; never Test."""

import json
import pickle
from pathlib import Path

import pytest

from risklab.config import load_model
from risklab.features.parity import verify_parity
from risklab.manifest import sha256_file
from risklab.models.candidate import ServingValidationConfig
from tests.conftest import CONFIG_DIR, REPO_ROOT

pytestmark = [pytest.mark.data, pytest.mark.slow]


def test_real_served_artifact_parity():
    candidate_path = REPO_ROOT / "reports/models/candidate.json"
    legacy_path = REPO_ROOT / "reports/models/serving_config.json"
    if candidate_path.is_file():
        candidate = json.loads(candidate_path.read_text())
        path = Path(candidate["model_path"])
        groups = candidate["feature_groups"]
    elif legacy_path.is_file():
        candidate = json.loads(legacy_path.read_text())
        groups = candidate["feature_groups"]
        path = legacy_path.with_name("champion.pkl")
    else:
        pytest.skip("no evaluated local model artifact available")
    if not path.is_file():
        if path.is_relative_to("/workspace"):
            path = REPO_ROOT / path.relative_to("/workspace")
        if not path.is_file():
            pytest.skip("local model artifact is absent")
    if candidate.get("model_sha256"):
        assert sha256_file(path) == candidate["model_sha256"]
    with open(path, "rb") as fh:
        model = pickle.load(fh)
    cfg = load_model(CONFIG_DIR, "serving_validation.yaml", ServingValidationConfig)
    result = verify_parity(
        model,
        groups,
        config_dir=CONFIG_DIR,
        root=REPO_ROOT,
        sample_size=cfg.parity_sample_size,
        seed=cfg.parity_seed,
        rtol=cfg.parity_rtol,
    )
    assert result["passed"], result


def test_frozen_model_external_scores_have_expected_mean_direction():
    import numpy as np

    from risklab.features.historical import load_full_frame
    from risklab.models.calibrate import predict_proba

    candidate_path = REPO_ROOT / "reports/models/candidate.json"
    if not candidate_path.is_file():
        pytest.skip("no verified local candidate")
    candidate = json.loads(candidate_path.read_text())
    path = Path(candidate["model_path"])
    if not path.is_file() and path.is_relative_to("/workspace"):
        path = REPO_ROOT / path.relative_to("/workspace")
    if not path.is_file():
        pytest.skip("local model artifact is absent")
    assert sha256_file(path) == candidate["model_sha256"]
    with open(path, "rb") as stream:
        model = pickle.load(stream)
    cfg = load_model(CONFIG_DIR, "serving_validation.yaml", ServingValidationConfig)
    frame, _ = load_full_frame("train", "fit", candidate["feature_groups"], config_dir=CONFIG_DIR)
    sample = frame.sample(n=min(cfg.parity_sample_size, len(frame)), random_state=cfg.parity_seed)
    sample = sample[list(model.feature_names_in_)]
    for column in ("EXT_SOURCE_1", "EXT_SOURCE_2", "EXT_SOURCE_3"):
        lower, higher = sample.copy(), sample.copy()
        lower[column], higher[column] = 0.2, 0.8
        assert np.mean(predict_proba(model, higher)) < np.mean(predict_proba(model, lower)), column
