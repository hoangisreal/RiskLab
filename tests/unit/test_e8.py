"""Synthetic temporal isolation, membership and uncertainty checks."""

import numpy as np
import pandas as pd
import pytest

from risklab.experiments.e8 import block_intervals, load_development, temporal_roles, weekly_metrics

SHARES = dict(train=0.55, calibration=0.10, selection=0.10, test=0.15, monitoring=0.10)


def test_week_roles_are_ordered_disjoint_and_buffered():
    roles = temporal_roles(list(range(92)), SHARES, 4)
    previous = None
    for weeks in roles.values():
        if previous is not None:
            assert min(weeks) - previous > 4
        previous = max(weeks)
    assert len({w for values in roles.values() for w in values}) == sum(map(len, roles.values()))


@pytest.mark.parametrize("role", ["test", "monitoring", "calibration"])
def test_reserved_label_roles_rejected_before_file_access(tmp_path, role):
    with pytest.raises(ValueError, match="only Train/Selection"):
        load_development(tmp_path, role, [80, 81], {})


def test_query_returns_only_development_labels(tmp_path):
    base = pd.DataFrame({"case_id": range(20), "WEEK_NUM": range(20), "target": [0, 1] * 10})
    base.to_parquet(tmp_path / "train_base.parquet")
    static = pd.DataFrame({"case_id": range(20), "amount": np.arange(20, dtype=float)})
    static.iloc[:10].to_parquet(tmp_path / "train_static_0_0.parquet")
    static.iloc[10:].to_parquet(tmp_path / "train_static_0_1.parquet")
    cfg = {"numeric": ["amount"], "categorical": [], "threads": 1, "seed": 7, "train_cap": 4}
    a = load_development(tmp_path, "train", list(range(8)), cfg)
    b = load_development(tmp_path, "selection", [10, 11, 12], cfg)
    assert len(a) == 4 and set(a.WEEK_NUM).issubset(range(8))
    assert set(b.WEEK_NUM) == {10, 11, 12} and set(b.case_id).isdisjoint(range(13, 20))


def test_weekly_single_class_is_not_assigned_an_auc():
    rows = weekly_metrics(
        np.array([0, 0, 0, 1]), np.array([0.1, 0.2, 0.3, 0.9]), np.array([1, 1, 2, 2])
    )
    assert rows[0]["metrics"] is None
    assert rows[1]["metrics"]["roc_auc"] == 1


def test_week_block_bootstrap_preserves_identical_candidate_differences():
    y = np.array([0, 1] * 6)
    p = np.linspace(0.1, 0.9, len(y))
    result = block_intervals(y, {"lightgbm": p, "copy": p}, np.repeat([1, 2, 3], 4), 7, 20)
    assert result["copy"]["paired_auc_delta_ci_vs_lightgbm"] == [0.0, 0.0]
