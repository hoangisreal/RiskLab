"""Check proposed demo snapshots against real feature code, not a mock business API."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from risklab.api.contract import request_model
from risklab.api.service import ServiceError, prepare_input
from risklab.api.validation import validate_history_records
from risklab.features.registry import load_registry
from tests.conftest import CONFIG_DIR, REPO_ROOT

pytestmark = pytest.mark.integration
FIXTURES = REPO_ROOT / "tests/fixtures/business_v1"


@pytest.fixture
def feature_root(tmp_path: Path) -> Path:
    """Keep DuckDB scratch files isolated; do not read any repository data rows."""
    shutil.copytree(CONFIG_DIR, tmp_path / "configs")
    shutil.copytree(REPO_ROOT / "sql", tmp_path / "sql")
    api_path = tmp_path / "configs/api.yaml"
    config = yaml.safe_load(api_path.read_text())
    config["duckdb"]["temp_directory"] = str(tmp_path / "duckdb_tmp")
    api_path.write_text(yaml.safe_dump(config))
    return tmp_path


@pytest.mark.parametrize(
    "snapshot_id,expected_count",
    [("demo-history", 1.0), ("demo-no-history", 0.0), ("demo-missing-history", None)],
)
def test_catalog_source_availability_through_shared_features(
    feature_root: Path, snapshot_id: str, expected_count: float | None
) -> None:
    catalog = json.loads((FIXTURES / "catalog.json").read_text())
    cfg = feature_root / "configs"
    payload = request_model(cfg).model_validate(catalog["snapshots"][snapshot_id])
    raw = payload.model_dump(exclude_none=True)
    groups = catalog["required_groups"]
    validate_history_records(raw["history"], groups)
    arguments = {
        "feature_groups": groups,
        "expected": None,
        "registry": load_registry(cfg),
        "root": feature_root,
        "config_dir": cfg,
    }
    if expected_count is None:
        with pytest.raises(ServiceError, match="missing required history source") as error:
            prepare_input(raw["application"], raw["history"], **arguments)
        assert error.value.status == 422
        return
    frame, warnings = prepare_input(raw["application"], raw["history"], **arguments)
    assert warnings == []
    assert len(frame) == 1
    assert "TARGET" not in frame and not any(c.startswith("SK_ID_") for c in frame)
    for column in ("bureau_record_count", "prev_record_count", "inst_record_count"):
        assert frame.iloc[0][column] == expected_count
    if expected_count == 0:
        # Confirmed no-history means zero counts, not fabricated zero-valued rates.
        assert pd.isna(frame.iloc[0]["inst_late_ratio"])
        assert pd.isna(frame.iloc[0]["prev_refusal_rate"])
    else:
        assert frame.iloc[0]["inst_late_ratio"] == 0


def test_invalid_history_is_not_published_in_catalog() -> None:
    invalid = json.loads((FIXTURES / "invalid-history.json").read_text())
    with pytest.raises(ValidationError) as error:
        request_model(CONFIG_DIR).model_validate(invalid)
    assert any(item["loc"][-1] == "DAYS_CREDIT" for item in error.value.errors())
    catalog = json.loads((FIXTURES / "catalog.json").read_text())
    assert invalid not in catalog["snapshots"].values()


def _rows(name: str) -> tuple[list[str], list[dict[str, str]]]:
    with (FIXTURES / name).open(newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def test_csv_examples_document_distinct_import_failures() -> None:
    """Check fixture intent only; the real importer must reuse these cases in B03."""
    catalog = json.loads((FIXTURES / "catalog.json").read_text())
    header, valid = _rows("import-valid.csv")
    assert header == ["external_reference", "snapshot_id", "supersedes_application_id"]
    assert len(valid) == 3
    assert {row["snapshot_id"] for row in valid} == set(catalog["snapshots"])
    assert len({row["external_reference"] for row in valid}) == len(valid)
    _, duplicate = _rows("import-duplicate-reference.csv")
    assert duplicate[0]["external_reference"] == duplicate[1]["external_reference"]
    _, unknown = _rows("import-unknown-snapshot.csv")
    assert unknown[0]["snapshot_id"] not in catalog["snapshots"]
    _, traversal = _rows("import-path-injection.csv")
    assert traversal[0]["snapshot_id"].startswith("../")
    assert traversal[0]["snapshot_id"] not in catalog["snapshots"]
    extra_header, _ = _rows("import-extra-column.csv")
    assert extra_header == [*header, "TARGET"]
