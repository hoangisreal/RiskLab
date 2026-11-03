"""Frozen alert-policy identity and predeclared independent seed ranges."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from risklab.config import load_model
from risklab.manifest import config_hash, write_json_atomic
from risklab.monitoring.policy import AlertPolicy, effective_config, scaled_config
from risklab.monitoring.profile import make_profile
from risklab.monitoring.service import MonitoringConfig
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.unit


def test_alert_policy_is_bound_to_model_profile_and_config(tmp_path):
    cfg = load_model(CONFIG_DIR, "monitoring.yaml", MonitoringConfig)
    profile = make_profile(
        pd.DataFrame({"x": np.arange(100)}),
        np.linspace(0, 1, 30),
        model_sha256="a" * 64,
        contract_version="1.1",
        bins=10,
        cdf_points=32,
    )
    path = tmp_path / "monitoring/alert-policy" / f"{profile.model_sha256}.json"
    policy = AlertPolicy(
        model_sha256=profile.model_sha256,
        profile_hash=config_hash(profile.model_dump()),
        config_hash=config_hash(cfg.model_dump()),
        scale=1.25,
        run_id="synthetic-control-run",
        calibration_batches=100,
        validation_batches=100,
        calibration_false_alarm_rate=0.03,
        validation_false_alarm_rate=0.04,
        passed=True,
    )
    write_json_atomic(path, policy.model_dump())
    effective, run_id = effective_config(cfg, profile, reports_dir=tmp_path)
    assert effective.psi_alert == cfg.psi_alert * 1.25
    assert effective.performance_ci_level == cfg.performance_ci_level
    assert run_id == policy.run_id
    changed = cfg.model_copy(update={"batch_size": cfg.batch_size + 1})
    with pytest.raises(ValueError, match="different model/profile/config"):
        effective_config(changed, profile, reports_dir=tmp_path)
    policy.passed = False
    write_json_atomic(path, policy.model_dump())
    with pytest.raises(ValueError, match="failed its held-out"):
        effective_config(cfg, profile, reports_dir=tmp_path)


def test_control_seeds_cannot_overlap():
    cfg = load_model(CONFIG_DIR, "monitoring.yaml", MonitoringConfig)
    with pytest.raises(ValidationError, match="disjoint"):
        MonitoringConfig.model_validate(
            cfg.model_dump() | {"validation_control_seed": cfg.calibration_control_seed}
        )
    assert scaled_config(cfg, 2).ks_alert == cfg.ks_alert * 2
