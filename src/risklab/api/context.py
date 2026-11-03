"""Cache immutable serving inputs while retaining a single batch/API column conversion."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from risklab.config import RawSchema, load_raw_schema
from risklab.data.clean import CleaningConfig, load_cleaning_config
from risklab.features.application import FeatureColumns, feature_spec, load_features_config


@lru_cache(maxsize=16)
def input_spec(config_dir: Path) -> tuple[RawSchema, CleaningConfig, FeatureColumns]:
    schema = load_raw_schema(config_dir)
    cleaning = load_cleaning_config(config_dir, schema)
    columns = feature_spec(load_features_config(config_dir), config_dir=config_dir)
    return schema, cleaning, columns
