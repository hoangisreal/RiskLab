"""Shared synthetic application recipe for local demos and browser tests.

Fits 500 synthetic Train rows with shared batch features. Generated outputs
belong to the caller's isolated directory and carry synthetic provenance.
"""

from __future__ import annotations

import hashlib
import pickle
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from risklab.business.catalog import DemoCatalog, PinnedRelease
from risklab.business.scoring import PinnedScoringModel, runtime_protocol_hashes
from risklab.business.storage import LocalStorage
from risklab.cli import app as cli_app
from risklab.config import (
    DataConfig,
    RawSchema,
    load_data_config,
    load_partition_config,
    load_raw_schema,
)
from risklab.features.application import FeatureColumns
from risklab.features.historical import combined_columns, load_full_frame
from risklab.manifest import library_versions
from risklab.models.pipelines import load_lgbm_params, make_lgbm_pipeline
from risklab.models.train import fit_pipeline
from risklab.synthetic import generate

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs"


def write_config_dir(root: Path, cfg: DataConfig, schema: RawSchema) -> Path:
    """Write ``root/configs/{data,raw_schema}.yaml``; mirrors the test helper."""
    cfg_dir = root / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "data.yaml").write_text(
        yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8"
    )
    (cfg_dir / "raw_schema.yaml").write_text(
        yaml.safe_dump(schema.model_dump(mode="json")), encoding="utf-8"
    )
    return cfg_dir


def build_scorer(root: Path) -> tuple[PinnedScoringModel, DemoCatalog, PinnedRelease, LocalStorage]:
    """Fit the proven synthetic recipe (500 Train rows) for local synthetic application runs."""
    repo_cfg = load_data_config(CONFIG_DIR)
    duck = repo_cfg.duckdb.model_copy(
        update={"temp_directory": root / "duck_tmp", "memory_limit": "1GB"}
    )
    cfg = repo_cfg.model_copy(
        update={
            "raw_dir": root / "raw",
            "parquet_dir": root / "parquet",
            "manifest_path": root / "manifest.json",
            "reports_dir": root / "reports",
            "duckdb": duck,
        }
    )
    schema = load_raw_schema(CONFIG_DIR)
    generate(cfg.raw_dir, schema, {t: f for f, t in cfg.tables.items()}, n_applicants=3000, seed=5)
    cfg_dir = write_config_dir(root, cfg, schema)
    for name in (
        "cleaning.yaml",
        "features_application.yaml",
        "features.yaml",
        "model_lr.yaml",
        "model_lgbm.yaml",
        "calibration.yaml",
        "cost.yaml",
        "monitoring.yaml",
        "serving_validation.yaml",
        "verification.yaml",
        "final_eval.yaml",
        "registry.yaml",
        "historical.yaml",
        "api.yaml",
        "application_validation.yaml",
        "application_domains.yaml",
    ):
        shutil.copy(CONFIG_DIR / name, cfg_dir / name)
    sql_link = root / "sql"
    if not sql_link.exists():
        sql_link.symlink_to(REPO_ROOT / "sql")
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
        result = runner.invoke(cli_app, ["--config-dir", str(cfg_dir), step])
        assert result.exit_code == 0, result.output
    data_cfg = load_data_config(cfg_dir)
    partition_cfg = load_partition_config(cfg_dir)
    X, y = load_full_frame(
        "train",
        "fit",
        ["bureau", "previous", "installments"],
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=cfg_dir,
    )
    numeric, categorical = combined_columns(
        ["bureau", "previous", "installments"], config_dir=cfg_dir
    )
    columns = FeatureColumns(numeric=numeric, categorical=categorical, log=[])
    pipe = fit_pipeline(
        make_lgbm_pipeline(load_lgbm_params(cfg_dir), columns=columns, config_dir=str(cfg_dir)),
        X[columns.all].head(500),
        y.head(500),
    )
    model_path = root / "model.pkl"
    payload = pickle.dumps(pipe)
    model_path.write_bytes(payload)
    release = PinnedRelease(
        model_version="synthetic-browser-v1",
        model_sha256=hashlib.sha256(payload).hexdigest(),
        model_run_id="synthetic-browser-run",
        lock_id="synthetic-browser-lock",
        feature_contract_version="1.1",
        feature_groups=["bureau", "previous", "installments"],
        threshold=0.1,
        policy_version="synthetic-policy-v1",
        protocol_hashes=runtime_protocol_hashes(root, cfg_dir),
        library_versions=library_versions(),
        feature_columns=list(pipe.feature_names_in_),
    )
    scorer = PinnedScoringModel(
        model_path=model_path, release=release, config_dir=cfg_dir, root=root
    )
    catalog_data = (REPO_ROOT / "tests/fixtures/business_v1/catalog.json").read_bytes()
    catalog = DemoCatalog(
        catalog_data, expected_sha256=hashlib.sha256(catalog_data).hexdigest(), config_dir=cfg_dir
    )
    return scorer, catalog, release, LocalStorage(root / "objects")
