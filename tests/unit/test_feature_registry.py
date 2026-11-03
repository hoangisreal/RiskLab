"""Registry and SQL hygiene guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from risklab.data.validate_history import sql_files_reference_target
from risklab.features.registry import load_registry

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_registry_groups_and_no_target_in_feature_sql() -> None:
    reg = load_registry(ROOT / "configs")
    assert reg.contract_version == "1.0"
    assert set(reg.all_groups()) == {
        "application",
        "bureau",
        "bureau_balance",
        "previous",
        "installments",
        "pos_cash",
        "credit_card",
    }
    for group in reg.all_groups():
        assert (ROOT / reg.groups[group].source_sql).is_file()
    assert sql_files_reference_target(ROOT / "sql" / "features") == []
    counts = [
        c for g in reg.all_groups() for c in reg.group_features([g]) if c.endswith("_record_count")
    ]
    assert len(counts) == 6  # every history group exposes its record count
