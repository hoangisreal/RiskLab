from __future__ import annotations

import shutil
from pathlib import Path
from typing import NamedTuple

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
from tests.conftest import CONFIG_DIR, write_config_dir
from tests.fixtures.synthetic import generate


class AppEnv(NamedTuple):
    config_dir: Path
    data_cfg: DataConfig
    partition_cfg: PartitionConfig


@pytest.fixture(scope="module")
def app_env(
    tmp_path_factory: pytest.TempPathFactory,
    repo_data_config: DataConfig,
    raw_schema: RawSchema,
) -> AppEnv:
    """Synthetic raw data ingested and partitioned under a temp dir, with the shipped configs."""
    root = tmp_path_factory.mktemp("app_env")
    duck = repo_data_config.duckdb.model_copy(
        update={"temp_directory": root / "duck_tmp", "memory_limit": "1GB"}
    )
    cfg = repo_data_config.model_copy(
        update={
            "raw_dir": root / "raw",
            "parquet_dir": root / "parquet",
            "manifest_path": root / "manifest.json",
            "reports_dir": root / "reports",
            "duckdb": duck,
        }
    )
    generate(
        cfg.raw_dir, raw_schema, {t: f for f, t in cfg.tables.items()}, n_applicants=3000, seed=5
    )
    cfg_dir = write_config_dir(root, cfg, raw_schema)
    for name in (
        "cleaning.yaml",
        "features_application.yaml",
        "features.yaml",
        "model_lr.yaml",
        "model_lgbm.yaml",
        "calibration.yaml",
        "cost.yaml",
    ):
        shutil.copy(CONFIG_DIR / name, cfg_dir / name)
    exp_dir = cfg_dir / "experiments"
    exp_dir.mkdir(exist_ok=True)
    for name in ("e1.yaml", "e2.yaml", "e3.yaml", "e4.yaml", "diagnostics.yaml"):
        shutil.copy(CONFIG_DIR / "experiments" / name, exp_dir / name)
    # Feature SQL lives at <root>/sql/features; link the shipped files for the online/batch paths.
    sql_link = root / "sql"
    if not sql_link.exists():
        sql_link.symlink_to(Path(__file__).resolve().parents[2] / "sql")
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
    return AppEnv(cfg_dir, load_data_config(cfg_dir), load_partition_config(cfg_dir))
