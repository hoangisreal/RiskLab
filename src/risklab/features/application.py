"""Application training frame and the feature policy shared by pipelines and the API contract.

Rows come only from ``risklab.data.loaders.load_partition`` (never the Test role), are cleaned
with ``clean_application`` and reduced to the model inputs. Everything here is row-wise or
schema-derived; no statistic across applicants is computed (those live in the sklearn pipelines,
fitted on Train only). Column types come from ``raw_schema.yaml``, never from data values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import Field, field_validator

from risklab.config import (
    ConfigError,
    DataConfig,
    PartitionConfig,
    RawSchema,
    _Strict,
    load_data_config,
    load_model,
    load_partition_config,
    load_raw_schema,
)
from risklab.data.clean import (
    APPLICATION_TABLE,
    CleaningConfig,
    clean_application,
    load_cleaning_config,
)
from risklab.data.loaders import PartitionRole, Purpose, load_partition

CATEGORICAL_TYPE = "VARCHAR"


class FeaturesConfig(_Strict):
    """Feature policy (``features_application.yaml``)."""

    exclude: list[str]
    log_prefix: str = Field(min_length=1)
    log_exclude_prefixes: list[str] = Field(default_factory=list)
    onehot_min_frequency: float = Field(gt=0, lt=1)
    lgbm_min_category_count: int = Field(ge=1)

    @field_validator("exclude")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("exclude contains duplicates")
        return v


@dataclass(frozen=True)
class FeatureColumns:
    """Model input columns in frame order. ``log`` is the subset of ``numeric`` to log-transform."""

    numeric: list[str]
    categorical: list[str]
    log: list[str]

    @property
    def all(self) -> list[str]:
        return [*self.numeric, *self.categorical]


def load_features_config(config_dir: Path | str | None = None) -> FeaturesConfig:
    return load_model(config_dir, "features_application.yaml", FeaturesConfig)


def resolve_columns(
    cfg: FeaturesConfig,
    schema: RawSchema,
    cleaning: CleaningConfig,
    *,
    id_column: str,
    target_column: str,
) -> FeatureColumns:
    """Derive the model input columns from the schema and cleaning config; validate the policy."""
    specs = schema.columns(APPLICATION_TABLE)
    names = {c.name for c in specs}
    flags = {r.flag_column for r in cleaning.sentinel_to_null}
    unknown = sorted(set(cfg.exclude) - names - flags)
    if unknown:
        raise ConfigError(
            f"features: excluded column(s) not in {APPLICATION_TABLE} or cleaning flags: {unknown}"
        )
    required = {id_column, target_column} - set(cfg.exclude)
    if required:
        raise ConfigError(f"features: exclude must contain the id and target: {sorted(required)}")

    kept = [c for c in specs if c.name not in cfg.exclude]
    categorical = [c.name for c in kept if c.type == CATEGORICAL_TYPE]
    numeric = [c.name for c in kept if c.type != CATEGORICAL_TYPE]
    numeric += [
        r.flag_column
        for r in cleaning.sentinel_to_null
        if r.column not in cfg.exclude and r.flag_column not in cfg.exclude
    ]

    log = [
        c
        for c in numeric
        if c.startswith(cfg.log_prefix) and not c.startswith(tuple(cfg.log_exclude_prefixes))
    ]
    if not log:
        raise ConfigError(f"features: log_prefix {cfg.log_prefix!r} matches no numeric column")
    return FeatureColumns(numeric=numeric, categorical=categorical, log=log)


def feature_spec(
    cfg: FeaturesConfig,
    *,
    config_dir: Path | str | None = None,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
) -> FeatureColumns:
    """Resolve the model input columns (numeric, categorical, log subset) for ``cfg``."""
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    schema = load_raw_schema(config_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(config_dir, schema)
    return resolve_columns(
        cfg,
        schema,
        cleaning,
        id_column=partition_cfg.id_column,
        target_column=partition_cfg.stratify_column,
    )


def feature_columns(
    cfg: FeaturesConfig,
    *,
    config_dir: Path | str | None = None,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
) -> tuple[list[str], list[str]]:
    """Return the (numeric, categorical) model column lists, the single source of truth."""
    spec = feature_spec(cfg, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg)
    return spec.numeric, spec.categorical


def load_application_frame(
    role: PartitionRole | str,
    purpose: Purpose | str,
    *,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    config_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Load one partition role as a cleaned feature frame and its target.

    X has the columns of ``feature_columns`` (numeric first, as float64 with NaN for missing;
    then categorical as object with NaN for missing). Both are indexed by the id column. Missing
    values are never coerced: a NULL stays NaN. y is TARGET as int64 with the same index.
    """
    data_cfg = data_cfg or load_data_config(config_dir)
    partition_cfg = partition_cfg or load_partition_config(config_dir)
    cfg = load_features_config(config_dir)
    cols = feature_spec(cfg, config_dir=config_dir, data_cfg=data_cfg, partition_cfg=partition_cfg)
    schema = load_raw_schema(config_dir, data_cfg=data_cfg)
    cleaning = load_cleaning_config(config_dir, schema)

    table = load_partition(
        role,
        purpose,
        data_cfg=data_cfg,
        partition_cfg=partition_cfg,
        config_dir=Path(config_dir) if config_dir is not None else None,
    )
    table = clean_application(table, cleaning)

    ids = pd.Index(table[partition_cfg.id_column].to_numpy(), name=partition_cfg.id_column)
    data: dict[str, pd.Series] = {}
    for name in cols.numeric:
        data[name] = pd.Series(
            table[name].to_numpy(zero_copy_only=False), index=ids, dtype="float64"
        )
    for name in cols.categorical:
        values = table[name].to_pylist()
        data[name] = pd.Series(
            [float("nan") if v is None else v for v in values], index=ids, dtype=object
        )
    X = pd.DataFrame(data, index=ids, columns=cols.all)
    y = pd.Series(
        table[partition_cfg.stratify_column].to_numpy(zero_copy_only=False),
        index=ids,
        dtype="int64",
        name=partition_cfg.stratify_column,
    )
    return X, y


__all__ = [
    "FeatureColumns",
    "FeaturesConfig",
    "feature_columns",
    "feature_spec",
    "load_application_frame",
    "load_features_config",
    "resolve_columns",
]
