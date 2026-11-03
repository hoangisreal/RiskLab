"""E2 marginal-gain rule: served flags must follow the marginal (vs previous) bootstrap."""

from __future__ import annotations

import pytest

from risklab.experiments.e234 import run_e2

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_e2_marginal_rule_consistency(app_env) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    summary = run_e2(config_dir=app_env.config_dir, root=Path(app_env.config_dir).parent)
    steps = summary["ablation"]
    assert steps[0]["step"] == "application"
    for entry in steps[1:]:
        marginal = entry["marginal"]
        expected = bool(marginal["delta"] >= summary["min_gain"] and marginal["ci_low"] > 0)
        assert entry["marginal_gain"] == pytest.approx(marginal["delta"])
        assert entry["served_by_rule"] is expected
    assert summary["served_groups"][0] == "application"
