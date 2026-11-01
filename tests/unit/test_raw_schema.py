from pathlib import Path

import pytest
import yaml

from risklab.config import ConfigError, DataConfig, RawSchema, load_raw_schema

pytestmark = pytest.mark.unit


def _write(tmp_path: Path, doc: object) -> Path:
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "raw_schema.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return cfg_dir


def test_repo_schema_covers_configured_tables(
    raw_schema: RawSchema, repo_data_config: DataConfig
) -> None:
    assert set(raw_schema.tables) == set(repo_data_config.tables.values())
    assert len(raw_schema.columns("application_train")) == 122
    assert len(raw_schema.columns("application_test")) == 121


def test_key_column_types(raw_schema: RawSchema) -> None:
    types = {c.name: c.type for c in raw_schema.columns("application_train")}
    assert types["SK_ID_CURR"] == "BIGINT"
    assert types["TARGET"] == "INTEGER"
    assert types["AMT_INCOME_TOTAL"] == "DOUBLE"
    assert types["CODE_GENDER"] == "VARCHAR"


def test_rejects_unknown_type(tmp_path: Path) -> None:
    cfg_dir = _write(tmp_path, {"tables": {"t": [{"name": "a", "type": "DECIMAL(5,2)"}]}})
    with pytest.raises(ConfigError, match="unsupported type"):
        load_raw_schema(cfg_dir)


def test_rejects_duplicate_columns_case_insensitive(tmp_path: Path) -> None:
    cols = [{"name": "a", "type": "BIGINT"}, {"name": "A", "type": "DOUBLE"}]
    with pytest.raises(ConfigError, match="duplicate column"):
        load_raw_schema(_write(tmp_path, {"tables": {"t": cols}}))


def test_rejects_extra_keys(tmp_path: Path) -> None:
    cols = [{"name": "a", "type": "BIGINT", "nullable": True}]
    with pytest.raises(ConfigError):
        load_raw_schema(_write(tmp_path, {"tables": {"t": cols}}))


def test_rejects_table_mismatch_with_data_config(
    tmp_path: Path, repo_data_config: DataConfig
) -> None:
    cfg_dir = _write(tmp_path, {"tables": {"only": [{"name": "a", "type": "BIGINT"}]}})
    with pytest.raises(ConfigError, match="missing"):
        load_raw_schema(cfg_dir, data_cfg=repo_data_config)


def test_table_hash_depends_on_order_and_type() -> None:
    def schema(cols: list[tuple[str, str]]) -> RawSchema:
        return RawSchema.model_validate(
            {"tables": {"t": [{"name": n, "type": t} for n, t in cols]}}
        )

    base = schema([("a", "BIGINT"), ("b", "DOUBLE")])
    assert base.table_hash("t") == schema([("a", "BIGINT"), ("b", "DOUBLE")]).table_hash("t")
    assert base.table_hash("t") != schema([("b", "DOUBLE"), ("a", "BIGINT")]).table_hash("t")
    assert base.table_hash("t") != schema([("a", "BIGINT"), ("b", "VARCHAR")]).table_hash("t")
