"""Combined training frames: application columns plus historical feature groups.

Application rows still come only through ``load_partition`` + ``clean_application``; the
historical aggregates join on SK_ID_CURR afterwards. Applicants without history keep
``<group>_record_count = 0`` and NULL aggregates, exactly as the SQL LEFT JOIN produces.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from risklab.config import DataConfig, PartitionConfig, load_data_config, load_partition_config
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.loaders import PartitionRole, Purpose, load_partition
from risklab.features.application import feature_spec, load_features_config
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
    from risklab.config import load_raw_schema, project_root, resolve_config_dir

    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    cfg_dir = resolve_config_dir(config_dir)
    root = root or project_root(cfg_dir)
    registry = registry or load_registry(cfg_dir)
    features_cfg = load_features_config(config_dir)
    cols = feature_spec(
        features_cfg, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg
    )
    schema = load_raw_schema(config_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(config_dir, schema)

    table = load_partition(
        role, purpose, data_cfg=data_cfg, partition_cfg=partition_cfg, config_dir=cfg_dir
    )
    cleaned = clean_application(table, cleaning)
    ids = cleaned[partition_cfg.id_column].to_pylist()
    hist = build_features(
        data_cfg=data_cfg,
        root=root,
        applicant_ids=ids,
        application_cleaned=cleaned,
        groups=list(groups),
        registry=registry,
    )
    hist_frame = combine_frames(hist, list(groups)) if groups else pd.DataFrame(index=pd.Index(ids))

    # Application part, mirroring load_application_frame conversions.
    id_index = pd.Index(cleaned[partition_cfg.id_column].to_numpy(), name=partition_cfg.id_column)
    data: dict[str, pd.Series] = {}
    for name in cols.numeric:
        data[name] = pd.Series(
            cleaned[name].to_numpy(zero_copy_only=False), index=id_index, dtype="float64"
        )
    for name in cols.categorical:
        values = cleaned[name].to_pylist()
        data[name] = pd.Series(
            [float("nan") if v is None else v for v in values], index=id_index, dtype=object
        )
    app = pd.DataFrame(data, index=id_index, columns=cols.all)
    hist_frame.index = hist_frame.index.astype(app.index.dtype)
    X = pd.concat([app, hist_frame.reindex(app.index)], axis=1)
    y = pd.Series(
        cleaned[partition_cfg.stratify_column].to_numpy(zero_copy_only=False),
        index=id_index,
        dtype="int64",
        name=partition_cfg.stratify_column,
    )
    return X, y


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
    numeric = list(spec.numeric) + registry.group_features(list(groups))
    return numeric, list(spec.categorical)


__all__ = ["combined_columns", "load_full_frame"]
