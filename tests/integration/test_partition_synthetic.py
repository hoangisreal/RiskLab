import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from risklab import cli
from risklab.cli import app
from risklab.config import (
    PARTITION_ROLES,
    DataConfig,
    PartitionConfig,
    RawSchema,
    load_data_config,
    load_partition_config,
)
from risklab.data import partition as partition_mod
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.partition import PartitionError, class_counts, read_role_ids, run_partition
from tests.conftest import write_config_dir
from tests.fixtures.synthetic import generate

pytestmark = pytest.mark.integration

SHARES = {"train": 0.55, "calibration": 0.10, "selection": 0.10, "test": 0.15, "monitoring": 0.10}


def write_partition_yaml(cfg_dir: Path, seed: int = 11) -> None:
    (cfg_dir / "partition.yaml").write_text(
        yaml.safe_dump(
            {
                "seed": seed,
                "shares": SHARES,
                "source_table": "application_train",
                "id_column": "SK_ID_CURR",
                "stratify_column": "TARGET",
                "output_dir": "partitions",
            }
        )
    )


@pytest.fixture
def env(
    tmp_path: Path, make_config: Callable[..., DataConfig], raw_schema: RawSchema
) -> tuple[Path, DataConfig, PartitionConfig]:
    cfg = make_config()
    names = {t: f for f, t in cfg.tables.items()}
    generate(cfg.raw_dir, raw_schema, names, n_applicants=1500, seed=5)
    cfg_dir = write_config_dir(tmp_path, cfg, raw_schema)
    write_partition_yaml(cfg_dir)
    result = CliRunner().invoke(app, ["--config-dir", str(cfg_dir), "ingest"])
    assert result.exit_code == 0, result.output
    return cfg_dir, load_data_config(cfg_dir), load_partition_config(cfg_dir)


def invoke(cfg_dir: Path, *args: str) -> Any:
    return CliRunner().invoke(app, ["--config-dir", str(cfg_dir), "partition", *args])


def source_rows(data_cfg: DataConfig) -> list[tuple[int, int]]:
    con = duckdb_connect(data_cfg.duckdb)
    rows = con.execute(
        "SELECT SK_ID_CURR, TARGET FROM read_parquet(?)",
        [str(data_cfg.parquet_dir / "application_train.parquet")],
    ).fetchall()
    con.close()
    return [(int(i), int(t)) for i, t in rows]


def role_ids(cfg: PartitionConfig) -> dict[str, list[int]]:
    return {
        r: read_role_ids(cfg.output_dir / f"{r}.parquet", cfg.id_column) for r in PARTITION_ROLES
    }


def test_partition_is_disjoint_complete_and_exactly_stratified(
    env: tuple[Path, DataConfig, PartitionConfig],
) -> None:
    cfg_dir, data_cfg, cfg = env
    result = invoke(cfg_dir)
    assert result.exit_code == 0, result.output
    rows = source_rows(data_cfg)
    target = dict(rows)
    roles = role_ids(cfg)
    flat = [i for ids in roles.values() for i in ids]
    assert len(flat) == len(set(flat)) and set(flat) == set(target)
    assert all(ids == sorted(ids) for ids in roles.values())
    doc = json.loads((cfg.output_dir / "partitions.json").read_text())
    for t in (0, 1):
        n_class = sum(1 for x in target.values() if x == t)
        expected = class_counts(n_class, SHARES)
        for role in PARTITION_ROLES:
            assert sum(1 for i in roles[role] if target[i] == t) == expected[role]
    prevalence = sum(target.values()) / len(target)
    for role in PARTITION_ROLES:
        stats = doc["roles"][role]
        assert stats["rows"] == len(roles[role])
        assert stats["positives"] == sum(target[i] for i in roles[role])
        # per-class rounding gives at most 2 rows (one per class) of deviation from the share
        assert abs(stats["positives"] - prevalence * stats["rows"]) <= 1 + 1e-9
    manifest = json.loads((data_cfg.reports_dir / "manifests" / "partition.json").read_text())
    assert manifest["outputs"]["partition_hash"] == doc["partition_hash"]
    assert manifest["inputs"]["application_train.csv"] == doc["source_csv_sha256"]


def test_rerun_is_unchanged_and_different_seed_needs_overwrite(
    env: tuple[Path, DataConfig, PartitionConfig],
) -> None:
    cfg_dir, _data_cfg, cfg = env
    assert invoke(cfg_dir).exit_code == 0
    first = role_ids(cfg)
    doc1 = json.loads((cfg.output_dir / "partitions.json").read_text())
    mtime = (cfg.output_dir / "train.parquet").stat().st_mtime_ns

    again = invoke(cfg_dir)
    assert again.exit_code == 0 and "unchanged" in again.output
    assert (cfg.output_dir / "train.parquet").stat().st_mtime_ns == mtime

    write_partition_yaml(cfg_dir, seed=12)
    blocked = invoke(cfg_dir)
    assert blocked.exit_code == 1 and "invalidates" in blocked.output
    assert role_ids(cfg) == first

    forced = invoke(cfg_dir, "--overwrite")
    assert forced.exit_code == 0 and "overwritten" in forced.output
    doc2 = json.loads((cfg.output_dir / "partitions.json").read_text())
    assert doc2["partition_hash"] != doc1["partition_hash"]
    assert role_ids(cfg) != first
    assert {r: s["rows"] for r, s in doc2["roles"].items()} == {
        r: s["rows"] for r, s in doc1["roles"].items()
    }
    assert {r: s["positives"] for r, s in doc2["roles"].items()} == {
        r: s["positives"] for r, s in doc1["roles"].items()
    }


def test_tampered_role_file_is_detected(env: tuple[Path, DataConfig, PartitionConfig]) -> None:
    cfg_dir, _data_cfg, cfg = env
    assert invoke(cfg_dir).exit_code == 0
    ids = role_ids(cfg)["test"]
    partition_mod._write_role_parquet(cfg.output_dir / "test.parquet", ids[:-1], cfg.id_column)
    result = invoke(cfg_dir)
    assert result.exit_code == 1 and "does not match" in result.output


def test_failed_staging_leaves_existing_outputs_untouched(
    env: tuple[Path, DataConfig, PartitionConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir, data_cfg, cfg = env
    assert invoke(cfg_dir).exit_code == 0
    before = {p.name: p.read_bytes() for p in cfg.output_dir.iterdir()}

    original = partition_mod._write_role_parquet
    calls = {"n": 0}

    def flaky(path: Path, ids: list[int], id_column: str) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        original(path, ids, id_column)

    monkeypatch.setattr(partition_mod, "_write_role_parquet", flaky)
    new_cfg = cfg.model_copy(update={"seed": cfg.seed + 1})
    with pytest.raises(PartitionError, match="disk full"):
        run_partition(new_cfg, data_cfg, root=cfg_dir.parent, overwrite=True)
    after = {p.name: p.read_bytes() for p in cfg.output_dir.iterdir()}
    assert after == before


def test_row_order_does_not_change_assignment(
    env: tuple[Path, DataConfig, PartitionConfig],
) -> None:
    _cfg_dir, data_cfg, cfg = env
    rows = source_rows(data_cfg)
    shuffled = rows[:]
    random.Random(1).shuffle(shuffled)
    a = partition_mod.assign_partitions(rows, cfg.seed, cfg.shares)
    b = partition_mod.assign_partitions(shuffled, cfg.seed, cfg.shares)
    assert a == b


def test_missing_manifest_and_row_mismatch(env: tuple[Path, DataConfig, PartitionConfig]) -> None:
    cfg_dir, data_cfg, _cfg = env
    manifest = json.loads(data_cfg.manifest_path.read_text())
    manifest["summary"]["tables"]["application_train"]["parquet_rows"] += 1
    data_cfg.manifest_path.write_text(json.dumps(manifest))
    bad_rows = invoke(cfg_dir)
    assert bad_rows.exit_code == 1 and "ingest manifest records" in bad_rows.output
    del manifest["inputs"]["application_train.csv"]
    data_cfg.manifest_path.write_text(json.dumps(manifest))
    assert "no entry" in invoke(cfg_dir).output
    data_cfg.manifest_path.unlink()
    assert "manifest not found" in invoke(cfg_dir).output


def test_stale_parquet_or_missing_parquet_hash_is_rejected(
    env: tuple[Path, DataConfig, PartitionConfig],
) -> None:
    cfg_dir, data_cfg, cfg = env
    original = data_cfg.manifest_path.read_text()
    manifest = json.loads(original)

    manifest["outputs"]["application_train.parquet"] = "0" * 64
    data_cfg.manifest_path.write_text(json.dumps(manifest))
    result = invoke(cfg_dir)
    assert result.exit_code == 1 and "risklab ingest" in result.output
    assert not (cfg.output_dir / "partitions.json").exists()

    del manifest["outputs"]["application_train.parquet"]
    data_cfg.manifest_path.write_text(json.dumps(manifest))
    result = invoke(cfg_dir)
    assert result.exit_code == 1 and "risklab ingest" in result.output

    data_cfg.manifest_path.write_text(original)
    parquet = data_cfg.parquet_dir / "application_train.parquet"
    parquet.write_bytes(parquet.read_bytes() + b"x")
    result = invoke(cfg_dir)
    assert result.exit_code == 1 and "stale or modified" in result.output


def test_cli_module_exposes_partition() -> None:
    assert hasattr(cli, "partition")


def test_tampered_role_file_and_recorded_hash_with_same_partition_hash(
    env: tuple[Path, DataConfig, PartitionConfig],
) -> None:
    cfg_dir, _data_cfg, cfg = env
    assert invoke(cfg_dir).exit_code == 0
    doc_path = cfg.output_dir / "partitions.json"
    ids = role_ids(cfg)["test"][:-1]
    partition_mod._write_role_parquet(cfg.output_dir / "test.parquet", ids, cfg.id_column)
    doc = json.loads(doc_path.read_text())
    doc["roles"]["test"]["ids_sha256"] = partition_mod.ids_sha256(ids)
    doc_path.write_text(json.dumps(doc))
    result = invoke(cfg_dir)
    assert result.exit_code == 1 and "stale or modified" in result.output


def test_document_missing_a_role_is_a_clean_error(
    env: tuple[Path, DataConfig, PartitionConfig],
) -> None:
    cfg_dir, data_cfg, cfg = env
    assert invoke(cfg_dir).exit_code == 0
    doc_path = cfg.output_dir / "partitions.json"
    doc = json.loads(doc_path.read_text())
    del doc["roles"]["test"]
    doc_path.write_text(json.dumps(doc))
    with pytest.raises(PartitionError, match="stale or modified"):
        run_partition(cfg, data_cfg, root=cfg_dir.parent)
    with pytest.raises(PartitionError, match="no ids_sha256 for role 'test'"):
        partition_mod.verify_outputs(cfg.output_dir, doc, cfg.id_column)


def test_missing_step_manifest_is_recreated(env: tuple[Path, DataConfig, PartitionConfig]) -> None:
    cfg_dir, data_cfg, cfg = env
    assert invoke(cfg_dir).exit_code == 0
    manifest = data_cfg.reports_dir / "manifests" / "partition.json"
    p_hash = json.loads((cfg.output_dir / "partitions.json").read_text())["partition_hash"]
    manifest.unlink()
    result = invoke(cfg_dir)
    assert result.exit_code == 0 and "step manifest refreshed" in result.output
    assert json.loads(manifest.read_text())["outputs"]["partition_hash"] == p_hash
    assert "step manifest refreshed" not in invoke(cfg_dir).output


def test_manifest_write_failure_is_a_clean_error(
    env: tuple[Path, DataConfig, PartitionConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir, _data_cfg, _cfg = env

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(partition_mod, "write_manifest", boom)
    result = invoke(cfg_dir)
    assert result.exit_code == 1 and "read-only" in result.output
    assert "Traceback" not in result.output


def test_stray_role_file_without_document_requires_overwrite(
    env: tuple[Path, DataConfig, PartitionConfig],
) -> None:
    cfg_dir, _data_cfg, cfg = env
    assert invoke(cfg_dir).exit_code == 0
    (cfg.output_dir / "partitions.json").unlink()
    blocked = invoke(cfg_dir)
    assert blocked.exit_code == 1 and "--overwrite" in blocked.output
    assert invoke(cfg_dir, "--overwrite").exit_code == 0
