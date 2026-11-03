"""Serving configuration, resolved once per immutable application config directory."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field

from risklab.config import DuckDBConfig, _Strict, load_model


class ApiConfig(_Strict):
    max_records_per_source: int = Field(ge=1)
    max_body_bytes: int = Field(ge=1)
    explanation_top_k: int = Field(ge=1)
    required_application_fields: list[str]
    duckdb: DuckDBConfig


@lru_cache(maxsize=16)
def load_api_config(config_dir: Path) -> ApiConfig:
    cfg = load_model(config_dir, "api.yaml", ApiConfig)
    temp = cfg.duckdb.temp_directory
    return cfg.model_copy(
        update={
            "duckdb": cfg.duckdb.model_copy(
                update={
                    "temp_directory": temp if temp.is_absolute() else config_dir.parent / temp,
                }
            )
        }
    )
