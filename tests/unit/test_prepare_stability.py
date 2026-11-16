"""Training archive allowlist and acquisition immutability."""

import zipfile

import pytest
from scripts.prepare_stability import prepare


def test_training_sources_are_extracted_and_manifest_is_immutable(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in ("train_base.parquet", "train_static_0_0.parquet", "train_static_0_1.parquet"):
        with zipfile.ZipFile(raw / (name + ".zip"), "w") as z:
            z.writestr(name, b"synthetic fixture bytes")
    (raw / "feature_definitions.csv").write_text("Variable,Description\n")
    manifest = prepare(tmp_path)
    assert manifest["labels_opened"] is False
    assert prepare(tmp_path) == manifest
    (raw / "train_base.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs"):
        prepare(tmp_path)


def test_archive_cannot_write_an_unexpected_member(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    with zipfile.ZipFile(raw / "train_base.parquet.zip", "w") as z:
        z.writestr("../outside", b"synthetic")
    with pytest.raises(ValueError, match="exactly"):
        prepare(tmp_path)
    assert not (tmp_path / "outside").exists()
