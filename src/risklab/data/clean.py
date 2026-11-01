"""Row-wise cleaning of application tables (docs/data_contract.md, "Cleaning rules").

Cleaning is declarative (``cleaning.yaml``) and executed by one generic SQL builder over an
in-memory DuckDB connection. It is strictly row-wise: every output value depends only on the same
row, never on a statistic across rows. That is why it is safe to apply before partitioning, and
why the online (API) path can reuse the same function on a single-row table. Rows are never
dropped, added or reordered. Column names are quoted as identifiers and rule values are bound
parameters, so no config value is interpolated into SQL text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from pydantic import Field

from risklab.config import ConfigError, RawSchema, _Strict, load_model
from risklab.data.ingest import quote_ident

APPLICATION_TABLE = "application_train"
INPUT_VIEW = "_risklab_clean_input"
FLAG_ARROW_TYPE = pa.int32()
NON_NUMERIC_TYPES = frozenset({"VARCHAR", "DATE", "BOOLEAN"})


class CleaningError(Exception):
    """Raised when a table cannot be cleaned with the given config (missing/colliding columns)."""


class SentinelRule(_Strict):
    column: str = Field(min_length=1)
    value: int
    flag_column: str = Field(min_length=1)


class CategoryRule(_Strict):
    value: str
    columns: list[str] = Field(min_length=1)


class CleaningConfig(_Strict):
    """Cleaning rules (``cleaning.yaml``)."""

    sentinel_to_null: list[SentinelRule] = Field(default_factory=list)
    category_to_null: CategoryRule | None = None


def check_cleaning_config(cfg: CleaningConfig, schema: RawSchema) -> None:
    """Check rule columns exist in ``application_train`` with a compatible type, no flag clashes."""
    types = {c.name: c.type for c in schema.columns(APPLICATION_TABLE)}
    existing = {name.lower() for name in types}
    for rule in cfg.sentinel_to_null:
        if rule.column not in types:
            raise ConfigError(f"cleaning: unknown column {rule.column!r} in {APPLICATION_TABLE}")
        if types[rule.column] in NON_NUMERIC_TYPES:
            raise ConfigError(
                f"cleaning: sentinel column {rule.column!r} is {types[rule.column]}, "
                "expected a numeric type"
            )
        key = rule.flag_column.lower()
        if key in existing:
            raise ConfigError(
                f"cleaning: flag column {rule.flag_column!r} collides with an existing column"
            )
        existing.add(key)
    if cfg.category_to_null is not None:
        for col in cfg.category_to_null.columns:
            if col not in types:
                raise ConfigError(f"cleaning: unknown column {col!r} in {APPLICATION_TABLE}")
            if types[col] != "VARCHAR":
                raise ConfigError(
                    f"cleaning: category column {col!r} is {types[col]}, expected VARCHAR"
                )


def load_cleaning_config(config_dir: Path | str | None, schema: RawSchema) -> CleaningConfig:
    """Load ``cleaning.yaml`` and check it against the raw schema."""
    cfg = load_model(config_dir, "cleaning.yaml", CleaningConfig)
    check_cleaning_config(cfg, schema)
    return cfg


def _build_sql(cfg: CleaningConfig, columns: list[str]) -> tuple[str, list[Any]]:
    """SELECT with the original columns in order (rules applied) followed by the flag columns."""
    # Rule values per column, in text order so the positional parameters line up.
    nulling: dict[str, list[Any]] = {}
    for rule in cfg.sentinel_to_null:
        nulling.setdefault(rule.column, []).append(rule.value)
    if cfg.category_to_null:
        for col in cfg.category_to_null.columns:
            nulling.setdefault(col, []).append(cfg.category_to_null.value)
    params: list[Any] = []
    select: list[str] = []
    for col in columns:
        q = quote_ident(col)
        if col in nulling:
            whens = " ".join(f"WHEN {q} = ? THEN NULL" for _ in nulling[col])
            params.extend(nulling[col])
            select.append(f"CASE {whens} ELSE {q} END AS {q}")
        else:
            select.append(q)
    for rule in cfg.sentinel_to_null:
        select.append(
            f"CAST(CASE WHEN {quote_ident(rule.column)} = ? THEN 1 ELSE 0 END AS INTEGER) "
            f"AS {quote_ident(rule.flag_column)}"
        )
        params.append(rule.value)
    return f"SELECT {', '.join(select)} FROM {quote_ident(INPUT_VIEW)}", params


def clean_application(table: pa.Table, cfg: CleaningConfig) -> pa.Table:
    """Replace sentinels and category placeholders with NULL and append indicator flags.

    Same rows in the same order, same original columns and types; ``SK_ID_CURR`` and ``TARGET``
    are passed through untouched. For each sentinel rule an int32 flag column is appended: 1 where
    the source equals the sentinel, otherwise 0 (also 0 where the source is NULL).
    """
    columns = list(table.column_names)
    rule_columns = [r.column for r in cfg.sentinel_to_null]
    if cfg.category_to_null:
        rule_columns += cfg.category_to_null.columns
    missing = sorted(set(rule_columns) - set(columns))
    if missing:
        raise CleaningError(f"Table is missing columns required by the cleaning config: {missing}")
    lowered = {c.lower() for c in columns}
    clashes = sorted(
        r.flag_column for r in cfg.sentinel_to_null if r.flag_column.lower() in lowered
    )
    if clashes:
        raise CleaningError(f"Flag columns already exist in the table: {clashes}")

    sql, params = _build_sql(cfg, columns)
    con = duckdb.connect(":memory:", config={"preserve_insertion_order": True})
    try:
        con.register(INPUT_VIEW, table)
        result = con.sql(sql, params=params).to_arrow_table()
    finally:
        con.close()
    target = pa.schema(
        [*table.schema, *(pa.field(r.flag_column, FLAG_ARROW_TYPE) for r in cfg.sentinel_to_null)]
    )
    return result.cast(target)


__all__ = [
    "CategoryRule",
    "CleaningConfig",
    "CleaningError",
    "SentinelRule",
    "check_cleaning_config",
    "clean_application",
    "load_cleaning_config",
]
