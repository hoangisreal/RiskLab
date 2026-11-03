"""Complete batch/API feature and prediction parity on 500 Train applicants; never Test."""

import json
import pickle
from pathlib import Path

import pytest

from risklab.config import load_model
from risklab.features.parity import verify_parity
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
        pytest.skip("local model artifact is absent")
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
