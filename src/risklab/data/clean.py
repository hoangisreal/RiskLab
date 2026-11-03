"""Row-wise cleaning of application tables (docs/data_contract.md, "Cleaning rules").

Cleaning uses configured Arrow element-wise operations. Every output depends only on the same
row, with no applicant-level statistics. Batch and serving share this implementation. Rows,
column order and original types are preserved, and sentinel flags have an explicit integer type.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from risklab.config import ConfigError, RawSchema, _Strict, load_model

APPLICATION_TABLE = "application_train"
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

    arrays = {name: table[name] for name in columns}
    for rule in cfg.sentinel_to_null:
        match = pc.fill_null(pc.equal(arrays[rule.column], rule.value), False)
        arrays[rule.flag_column] = pc.cast(match, FLAG_ARROW_TYPE)
        arrays[rule.column] = pc.if_else(match, None, arrays[rule.column])
    if cfg.category_to_null:
        for name in cfg.category_to_null.columns:
            match = pc.fill_null(pc.equal(arrays[name], cfg.category_to_null.value), False)
            arrays[name] = pc.if_else(match, None, arrays[name])
    result = pa.table(arrays)
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
