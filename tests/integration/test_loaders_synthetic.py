import json
from collections.abc import Callable
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml
from typer.testing import CliRunner

from risklab.cli import app
from risklab.config import (
    DataConfig,
    PartitionConfig,
    RawSchema,
    load_data_config,
    load_partition_config,
)
from risklab.data import partition as partition_mod
from risklab.data.loaders import (
    ALLOWED_PURPOSE,
    LoaderError,
    PartitionRole,
    Purpose,
    TestAccessError,
    load_partition,
    partition_info,
)
from risklab.data.partition import read_role_ids
from tests.conftest import write_config_dir
from tests.fixtures.synthetic import generate

pytestmark = pytest.mark.integration

Env = tuple[Path, DataConfig, PartitionConfig]
READABLE = list(ALLOWED_PURPOSE.items())


@pytest.fixture
def env(tmp_path: Path, make_config: Callable[..., DataConfig], raw_schema: RawSchema) -> Env:
    cfg = make_config()
    names = {t: f for f, t in cfg.tables.items()}
    generate(cfg.raw_dir, raw_schema, names, n_applicants=1500, seed=5)
    cfg_dir = write_config_dir(tmp_path, cfg, raw_schema)
    (cfg_dir / "partition.yaml").write_text(
        yaml.safe_dump(
            {
                "seed": 11,
                "shares": {
                    "train": 0.55,
                    "calibration": 0.10,
                    "selection": 0.10,
                    "test": 0.15,
                    "monitoring": 0.10,
                },
                "source_table": "application_train",
                "id_column": "SK_ID_CURR",
                "stratify_column": "TARGET",
                "output_dir": "partitions",
            }
        )
    )
    runner = CliRunner()
    for step in ("ingest", "partition"):
        result = runner.invoke(app, ["--config-dir", str(cfg_dir), step])
        assert result.exit_code == 0, result.output
    return cfg_dir, load_data_config(cfg_dir), load_partition_config(cfg_dir)


def load(env: Env, role: PartitionRole, purpose: Purpose, **kw):  # type: ignore[no-untyped-def]
    cfg_dir, data_cfg, cfg = env
    return load_partition(
        role, purpose, data_cfg=data_cfg, partition_cfg=cfg, config_dir=cfg_dir, **kw
    )


def source_target(data_cfg: DataConfig) -> dict[int, int]:
    t = pq.read_table(
        data_cfg.parquet_dir / "application_train.parquet", columns=["SK_ID_CURR", "TARGET"]
    )
    return dict(zip(t["SK_ID_CURR"].to_pylist(), t["TARGET"].to_pylist(), strict=True))


@pytest.mark.parametrize(("role", "purpose"), READABLE)
def test_returns_exactly_the_role_ids_with_correct_target(
    env: Env, role: PartitionRole, purpose: Purpose
) -> None:
    _cfg_dir, data_cfg, cfg = env
    table = load(env, role, purpose)
    ids = table["SK_ID_CURR"].to_pylist()
    assert ids == sorted(ids)
    assert ids == read_role_ids(cfg.output_dir / f"{role.value}.parquet", "SK_ID_CURR")
    target = source_target(data_cfg)
    assert table["TARGET"].to_pylist() == [target[i] for i in ids]
    assert table.column_names[:2] == ["SK_ID_CURR", "TARGET"]
    assert table.num_columns > 2
    info = partition_info(role, data_cfg=data_cfg, partition_cfg=cfg, config_dir=env[0])
    assert (info.rows, info.positives) == (len(ids), sum(target[i] for i in ids))
    doc = json.loads((cfg.output_dir / "partitions.json").read_text())
    assert info.partition_hash == doc["partition_hash"]
    assert info.ids_sha256 == doc["roles"][role.value]["ids_sha256"]


def test_column_subset_keeps_id_and_target_first(env: Env) -> None:
    table = load(env, PartitionRole.train, Purpose.fit, columns=["AMT_CREDIT", "TARGET"])
    assert table.column_names == ["SK_ID_CURR", "TARGET", "AMT_CREDIT"]


def test_roles_never_contain_test_ids(env: Env) -> None:
    _cfg_dir, _data_cfg, cfg = env
    test_ids = set(read_role_ids(cfg.output_dir / "test.parquet", "SK_ID_CURR"))
    assert test_ids
    for role, purpose in READABLE:
        got = set(load(env, role, purpose, columns=[])["SK_ID_CURR"].to_pylist())
        assert not got & test_ids


def test_test_role_is_rejected_even_with_valid_setup(env: Env) -> None:
    for purpose in Purpose:
        with pytest.raises(TestAccessError):
            load(env, PartitionRole.test, purpose)


def test_wrong_purpose_is_rejected(env: Env) -> None:
    with pytest.raises(LoaderError, match="may only be read for purpose 'fit'"):
        load(env, PartitionRole.train, Purpose.select)
    with pytest.raises(LoaderError, match="Unknown purpose"):
        load(env, PartitionRole.train, "evaluate")  # type: ignore[arg-type]


def test_unknown_column_is_rejected(env: Env) -> None:
    with pytest.raises(LoaderError, match="Unknown column"):
        load(env, PartitionRole.train, Purpose.fit, columns=['x" FROM t; --'])


def test_tampered_role_file_is_rejected(env: Env) -> None:
    _cfg_dir, _data_cfg, cfg = env
    ids = read_role_ids(cfg.output_dir / "train.parquet", "SK_ID_CURR")
    partition_mod._write_role_parquet(cfg.output_dir / "train.parquet", ids[:-1], "SK_ID_CURR")
    with pytest.raises(LoaderError, match="does not match"):
        load(env, PartitionRole.train, Purpose.fit)


def test_tampered_test_role_file_also_invalidates_other_roles(env: Env) -> None:
    # partition_hash covers all five roles, so a modified Test file fails every load.
    _cfg_dir, _data_cfg, cfg = env
    ids = read_role_ids(cfg.output_dir / "test.parquet", "SK_ID_CURR")
    partition_mod._write_role_parquet(cfg.output_dir / "test.parquet", ids[:-1], "SK_ID_CURR")
    with pytest.raises(LoaderError, match="does not match"):
        load(env, PartitionRole.selection, Purpose.select)


def test_modified_document_hash_is_rejected(env: Env) -> None:
    _cfg_dir, _data_cfg, cfg = env
    path = cfg.output_dir / "partitions.json"
    doc = json.loads(path.read_text())
    doc["seed"] += 1
    path.write_text(json.dumps(doc))
    with pytest.raises(LoaderError, match="recomputed"):
        load(env, PartitionRole.train, Purpose.fit)


def test_missing_partitions_json_is_rejected(env: Env) -> None:
    _cfg_dir, _data_cfg, cfg = env
    (cfg.output_dir / "partitions.json").unlink()
    with pytest.raises(LoaderError, match="not found"):
        load(env, PartitionRole.train, Purpose.fit)


def test_modified_source_parquet_is_rejected(env: Env) -> None:
    _cfg_dir, data_cfg, _cfg = env
    parquet = data_cfg.parquet_dir / "application_train.parquet"
    parquet.write_bytes(parquet.read_bytes() + b"x")
    with pytest.raises(LoaderError, match="stale or modified"):
        load(env, PartitionRole.train, Purpose.fit)


def test_source_changed_since_partitioning_is_rejected(env: Env) -> None:
    _cfg_dir, data_cfg, _cfg = env
    manifest = json.loads(data_cfg.manifest_path.read_text())
    manifest["inputs"]["application_train.csv"] = "0" * 64
    data_cfg.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(LoaderError, match="different source CSV"):
        load(env, PartitionRole.train, Purpose.fit)
