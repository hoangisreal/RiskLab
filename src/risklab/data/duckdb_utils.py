"""DuckDB connection helper that applies the configured resource limits."""

from __future__ import annotations

import duckdb

from risklab.config import DuckDBConfig


def duckdb_connect(cfg: DuckDBConfig, database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with memory limit, spill directory, and threads from config."""
    cfg.temp_directory.mkdir(parents=True, exist_ok=True)
    settings: dict[str, str | bool | int | float | list[str]] = {
        "memory_limit": cfg.memory_limit,
        "temp_directory": str(cfg.temp_directory),
        "preserve_insertion_order": cfg.preserve_insertion_order,
    }
    if cfg.threads is not None:
        settings["threads"] = cfg.threads
    return duckdb.connect(database, config=settings)
