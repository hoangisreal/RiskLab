"""Combined training frames: application columns plus historical feature groups.

Application rows still come only through ``load_partition`` + ``clean_application``; the
historical aggregates join on SK_ID_CURR afterwards. Applicants without history keep
``<group>_record_count = 0`` and NULL aggregates, exactly as the SQL LEFT JOIN produces.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pyarrow as pa

from risklab.config import DataConfig, PartitionConfig, load_data_config, load_partition_config
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.loaders import PartitionRole, Purpose, load_partition
from risklab.features.application import feature_spec, frame_from_table, load_features_config
from risklab.features.build import build_features, combine_frames
from risklab.features.registry import FeatureRegistry, load_registry


def load_full_frame(
    role: PartitionRole | str,
    purpose: Purpose | str,
    groups: Sequence[str],
    *,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    config_dir: Path | str | None = None,
    root: Path | None = None,
    registry: FeatureRegistry | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Application frame for ``role`` joined with the historical ``groups`` features."""
    from risklab.config import project_root, resolve_config_dir

    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    cfg_dir = resolve_config_dir(config_dir)
    root = root or project_root(cfg_dir)
    registry = registry or load_registry(cfg_dir)
    table = load_partition(
        role, purpose, data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=cfg_dir
    )
    X = _engineer(
        table,
        groups,
        data_cfg=data_cfg,
        root=root,
        config_dir=cfg_dir,
        partition_cfg=partition_cfg,
        registry=registry,
    )
    y = pd.Series(
        table[partition_cfg.stratify_column].to_numpy(zero_copy_only=False),
        index=X.index,
        dtype="int64",
        name=partition_cfg.stratify_column,
    )
    return X, y


def _engineer(
    table: pa.Table,
    groups: Sequence[str],
    *,
    data_cfg: DataConfig,
    root: Path,
    config_dir: Path,
    partition_cfg: PartitionConfig,
    registry: FeatureRegistry,
) -> pd.DataFrame:
    from risklab.config import load_raw_schema

    cols = feature_spec(
        load_features_config(config_dir),
        config_dir=config_dir,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
    )
    schema = load_raw_schema(config_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(config_dir, schema)
    cleaned = clean_application(table, cleaning)
    ids = cleaned[partition_cfg.id_column].to_pylist()
    engineered = ["application"] + [g for g in groups if g != "application"]
    hist = build_features(
        data_cfg=data_cfg,
        root=root,
        applicant_ids=ids,
        application_cleaned=cleaned,
        groups=engineered,
        registry=registry,
    )
    hist_frame = combine_frames(hist, engineered)

    # Application part, mirroring load_application_frame conversions.
    app = frame_from_table(cleaned, cols, id_column=partition_cfg.id_column)
    hist_frame.index = hist_frame.index.astype(app.index.dtype)
    return pd.concat([app, hist_frame.reindex(app.index)], axis=1)


def load_unlabeled_frame(
    groups: Sequence[str], *, config_dir: Path, root: Path, sample_size: int, seed: int
) -> pd.DataFrame:
    """Read only Kaggle application_test, with source verification and shared features."""
    import json

    from risklab.data.duckdb_utils import duckdb_connect
    from risklab.manifest import sha256_file

    cfg = load_data_config(config_dir)
    partition = load_partition_config(config_dir)
    document = json.loads(cfg.manifest_path.read_text())
    raw_name = next(name for name, table in cfg.tables.items() if table == "application_test")
    path = cfg.parquet_dir / "application_test.parquet"
    if (
        sha256_file(path) != document["outputs"][path.name]
        or sha256_file(cfg.raw_dir / raw_name) != document["inputs"][raw_name]
    ):
        raise ValueError("unlabeled application snapshot differs from ingest")
    con = duckdb_connect(cfg.duckdb)
    try:
        table = con.sql(
            "SELECT * FROM read_parquet(?) ORDER BY hash(SK_ID_CURR, ?) LIMIT ?",
            params=[str(path), seed, sample_size],
        ).to_arrow_table()
    finally:
        con.close()
    if "TARGET" in table.column_names:
        raise ValueError("Kaggle monitoring sample unexpectedly contains labels")
    return _engineer(
        table,
        groups,
        data_cfg=cfg,
        root=root,
        config_dir=config_dir,
        partition_cfg=partition,
        registry=load_registry(config_dir),
    )


def combined_columns(
    groups: Sequence[str],
    *,
    config_dir: Path | str | None = None,
    registry: FeatureRegistry | None = None,
) -> tuple[list[str], list[str]]:
    """(numeric, categorical) column lists for application + historical groups."""
    from risklab.features.application import feature_spec, load_features_config

    registry = registry or load_registry(config_dir)
    spec = feature_spec(load_features_config(config_dir), config_dir=config_dir)
    numeric = list(spec.numeric) + registry.group_features(
        ["application"] + [g for g in groups if g != "application"]
    )
    return numeric, list(spec.categorical)


__all__ = ["combined_columns", "load_full_frame"]
