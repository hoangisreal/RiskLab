"""Mutated lock contents, runtime, SQL, and dirty trees cannot reach final evaluation."""

import json

import pytest

from risklab.models import select

pytestmark = pytest.mark.unit


@pytest.fixture
def lock_env(tmp_path, monkeypatch):
    monkeypatch.setattr(select, "git_info", lambda cwd=None: {"commit": "commit", "dirty": False})
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/rules.yaml").write_text("seed: 1\n")
    model = tmp_path / "model.pkl"
    model.write_bytes(b"not unpickled")
    lock = select.create_lock(
        locks_dir=tmp_path / "locks",
        model_run_id="run",
        feature_set="test-fixture",
        feature_groups=[],
        contract_version="1.1",
        calibration_method="none",
        threshold=0.1,
        config={},
        partition_hash="partition",
        data_manifest_hash="data",
        model_path=model,
        root=tmp_path,
    )
    return tmp_path, lock


@pytest.mark.parametrize("change", ["dirty", "runtime", "sql", "artifact", "config"])
def test_changed_lock_context_is_rejected(lock_env, monkeypatch, change):
    root, lock = lock_env
    select.verify_lock(lock, root=root)
    if change == "dirty":
        monkeypatch.setattr(
            select, "git_info", lambda cwd=None: {"commit": "commit", "dirty": True}
        )
    elif change == "runtime":
        monkeypatch.setattr(select, "library_versions", lambda: {"python": "wrong"})
    elif change == "sql":
        (root / "configs/rules.yaml").write_text("seed: 2\n")
    elif change == "artifact":
        (root / "model.pkl").write_bytes(b"changed")
    else:
        lock["config"] = {"changed": True}
    with pytest.raises(select.SelectionError):
        select.verify_lock(lock, root=root)


def test_lock_id_binds_threshold_and_metadata(lock_env):
    root, lock = lock_env
    path = root / "locks" / f"{lock['lock_id']}.json"
    lock["threshold"] = 0.9
    path.write_text(json.dumps(lock))
    with pytest.raises(select.SelectionError, match="content hash"):
        select.load_lock(path.parent, lock["lock_id"])


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_threshold_cannot_be_locked(lock_env, threshold):
    root, _ = lock_env
    with pytest.raises(select.SelectionError, match="threshold"):
        select.create_lock(
            locks_dir=root / "locks",
            model_run_id="run",
            feature_set="fixture",
            feature_groups=[],
            contract_version="1.1",
            calibration_method="none",
            threshold=threshold,
            config={},
            partition_hash="partition",
            data_manifest_hash="data",
            model_path=root / "model.pkl",
            root=root,
        )


@pytest.mark.parametrize("change", ["partition", "manifest", "raw", "parquet"])
def test_changed_partition_and_data_cannot_pass_lock(lock_env, make_config, monkeypatch, change):
    from risklab.config import PartitionConfig
    from risklab.data import partition
    from risklab.manifest import sha256_file

    root, lock = lock_env
    cfg = make_config()
    cfg.raw_dir.mkdir()
    cfg.parquet_dir.mkdir()
    raw = cfg.raw_dir / "application_train.csv"
    parquet = cfg.parquet_dir / "application_train.parquet"
    raw.write_bytes(b"synthetic raw")
    parquet.write_bytes(b"synthetic parquet bytes; never decoded")
    cfg.manifest_path.write_text(
        json.dumps(
            {
                "inputs": {raw.name: sha256_file(raw)},
                "outputs": {parquet.name: sha256_file(parquet)},
            }
        )
    )
    lock["data_manifest_hash"] = sha256_file(cfg.manifest_path)
    part = PartitionConfig(
        seed=1,
        shares={
            "train": 0.55,
            "calibration": 0.1,
            "selection": 0.1,
            "test": 0.15,
            "monitoring": 0.1,
        },
        source_table="application_train",
        id_column="SK_ID_CURR",
        stratify_column="TARGET",
        output_dir=root / "partitions",
    )
    document = {"partition_hash": lock["partition_hash"]}
    monkeypatch.setattr(partition, "load_verified_document", lambda *a: document)
    select.verify_lock(lock, root=root, data_cfg=cfg, partition_cfg=part)
    if change == "partition":
        document["partition_hash"] = "changed"
    elif change == "manifest":
        cfg.manifest_path.write_text("{}")
    elif change == "raw":
        raw.write_bytes(b"changed")
    else:
        parquet.write_bytes(b"changed")
    with pytest.raises(select.SelectionError):
        select.verify_lock(lock, root=root, data_cfg=cfg, partition_cfg=part)
