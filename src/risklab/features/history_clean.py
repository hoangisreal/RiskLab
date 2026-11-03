"""Applicant-local historical cleaning shared by batch and serving connections."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import duckdb
from pydantic import Field

from risklab.config import RawSchema, _Strict, load_model
from risklab.data.ingest import quote_ident, quote_str


class HistoryTableRules(_Strict):
    non_future_columns: list[str]
    required_event_columns: list[str]
    sentinel_columns: list[str]


class HistoricalConfig(_Strict):
    xna_value: str
    sentinel_value: int
    installment_amount_tolerance: float = Field(ge=0, allow_inf_nan=False)
    tables: dict[str, HistoryTableRules]
    validation_sql: str
    error_checks: list[str]


@lru_cache(maxsize=16)
def load_historical_config(config_dir: Path) -> HistoricalConfig:
    return load_model(config_dir, "historical.yaml", HistoricalConfig)


def install_history_views(
    con: duckdb.DuckDBPyConnection,
    *,
    config_dir: Path,
    schema: RawSchema,
) -> None:
    """Create cleaned views from ``raw_<table>`` views without using labels or other applicants."""
    cfg = load_historical_config(config_dir)
    for table, rules in cfg.tables.items():
        columns = schema.columns(table)
        known = {c.name for c in columns}
        requested = set(
            rules.non_future_columns + rules.required_event_columns + rules.sentinel_columns
        )
        if not requested <= known:
            raise ValueError(
                f"unknown historical cleaning columns for {table}: {requested - known}"
            )
        expressions = []
        for col in columns:
            name = quote_ident(col.name)
            if col.name in rules.sentinel_columns:
                expressions.append(f"NULLIF({name}, {cfg.sentinel_value}) AS {name}")
            elif col.type == "VARCHAR":
                expressions.append(f"NULLIF({name}, {quote_str(cfg.xna_value)}) AS {name}")
            else:
                expressions.append(name)
        conditions = [
            f"({quote_ident(c)} IS NULL OR {quote_ident(c)} <= 0)" for c in rules.non_future_columns
        ]
        conditions += [f"{quote_ident(c)} IS NOT NULL" for c in rules.required_event_columns]
        where = " AND ".join(conditions) or "TRUE"
        con.execute(
            f"CREATE OR REPLACE VIEW {quote_ident(table)} AS SELECT "
            f"{', '.join(expressions)} FROM {quote_ident('raw_' + table)} WHERE {where}"
        )
