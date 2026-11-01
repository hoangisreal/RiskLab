from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from risklab.config import DataConfig, RawSchema, load_data_config, load_raw_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs"


@pytest.fixture(scope="session")
def repo_data_config() -> DataConfig:
    return load_data_config(CONFIG_DIR)


@pytest.fixture(scope="session")
def raw_schema(repo_data_config: DataConfig) -> RawSchema:
    return load_raw_schema(CONFIG_DIR, data_cfg=repo_data_config)


@pytest.fixture
def make_config(tmp_path: Path, repo_data_config: DataConfig) -> Callable[..., DataConfig]:
    """Build a DataConfig whose raw, parquet, report and spill paths live under ``tmp_path``."""

    def _make(tables: dict[str, str] | None = None) -> DataConfig:
        duck = repo_data_config.duckdb.model_copy(
            update={"temp_directory": tmp_path / "duck_tmp", "memory_limit": "1GB"}
        )
        return repo_data_config.model_copy(
            update={
                "raw_dir": tmp_path / "raw",
                "parquet_dir": tmp_path / "parquet",
                "manifest_path": tmp_path / "manifest.json",
                "reports_dir": tmp_path / "reports",
                "tables": tables or repo_data_config.tables,
                "duckdb": duck,
            }
        )

    return _make


def write_config_dir(root: Path, cfg: DataConfig, schema: RawSchema) -> Path:
    """Write ``root/configs/{data,raw_schema}.yaml`` for CLI tests and return the directory."""
    cfg_dir = root / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "data.yaml").write_text(
        yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )
    (cfg_dir / "raw_schema.yaml").write_text(
        yaml.safe_dump(schema.model_dump(mode="json")), encoding="utf-8"
    )
    return cfg_dir
