"""References cannot pass vacuously or ignore changed hashes and metrics."""

import json

import pytest

from risklab.verify import snapshot_reference, verify_reference

pytestmark = pytest.mark.unit


def test_missing_empty_and_malformed_reference_fail(tmp_path):
    assert not verify_reference(tmp_path / "missing", tmp_path)["passed"]
    ref = tmp_path / "ref"
    ref.mkdir()
    assert not verify_reference(ref, tmp_path)["passed"]
    (ref / "bad.json").write_text("broken json")
    assert not verify_reference(ref, tmp_path)["passed"]


@pytest.mark.parametrize("field", ["inputs", "outputs", "partition_hash"])
def test_changed_provenance_fails(tmp_path, field):
    ref, fresh = tmp_path / "ref", tmp_path / "fresh"
    ref.mkdir()
    fresh.mkdir()
    expected = {field: {"raw": "original"} if field != "partition_hash" else "original"}
    (ref / "x.json").write_text(json.dumps(expected))
    (fresh / "x.json").write_text(json.dumps(expected))
    assert verify_reference(ref, fresh)["passed"]
    (fresh / "x.json").write_text(json.dumps({field: "changed"}))
    assert not verify_reference(ref, fresh)["passed"]


def test_metrics_tolerance_and_path_traversal(tmp_path):
    ref, fresh = tmp_path / "ref", tmp_path / "fresh"
    ref.mkdir()
    fresh.mkdir()
    path = ref / "x.json"
    spec = {
        "path": "summary.json",
        "kind": "metrics",
        "expected": {"selection_metrics": {"auc": 0.75}},
    }
    path.write_text(json.dumps(spec))
    target = fresh / "summary.json"
    target.write_text(json.dumps({"selection_metrics": {"auc": 0.7500001}}))
    assert verify_reference(ref, fresh)["passed"]
    target.write_text(json.dumps({"selection_metrics": {"auc": 0.76}}))
    assert not verify_reference(ref, fresh)["passed"]
    spec["path"] = "../outside.json"
    path.write_text(json.dumps(spec))
    assert not verify_reference(ref, fresh)["passed"]


def test_untracked_metrics_cannot_become_a_published_reference(tmp_path):
    reports = tmp_path / "reports"
    source = reports / "experiments/e1/summary.json"
    source.parent.mkdir(parents=True)
    document = {"selection_metrics": {"model": {"roc_auc": 0.7}}}
    source.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="no tracked run"):
        snapshot_reference(reports, tmp_path / "reference")
    document["run_ids"] = {"model": "synthetic-source-run"}
    source.write_text(json.dumps(document))
    snapshot_reference(reports, tmp_path / "reference")
    result = json.loads((tmp_path / "reference/e1.json").read_text())
    assert result["source_run_ids"] == document["run_ids"]
