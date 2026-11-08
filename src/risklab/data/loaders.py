"""Role-gated reads of labeled rows.

``load_partition`` is the only sanctioned way to read labeled rows by partition role. The Test
role is never readable here: it is reserved for ``risklab.models.final_eval`` with a selection
lock. This module has an explicit path map for the four non-Test roles and never builds the
path of the Test role file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from risklab.config import (
    ConfigError,
    DataConfig,
    PartitionConfig,
    load_data_config,
    load_partition_config,
    load_raw_schema,
)
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import quote_ident
from risklab.data.partition import PartitionError, load_verified_document


class LoaderError(Exception):
    """Raised when a partition cannot be loaded or fails an integrity check."""


class TestAccessError(LoaderError):
    """Raised on every attempt to read the Test role through this module."""

    __test__ = False  # not a pytest test class despite the name


class PartitionRole(StrEnum):
    train = "train"
    calibration = "calibration"
    selection = "selection"
    test = "test"
    monitoring = "monitoring"


class Purpose(StrEnum):
    fit = "fit"
    calibrate = "calibrate"
    select = "select"
    monitor = "monitor"


# Each role may be read only for its own purpose. Test has no entry by design.
ALLOWED_PURPOSE: dict[PartitionRole, Purpose] = {
    PartitionRole.train: Purpose.fit,
    PartitionRole.calibration: Purpose.calibrate,
    PartitionRole.selection: Purpose.select,
    PartitionRole.monitoring: Purpose.monitor,
}

# Explicit role files for the readable roles. Do not derive these from the role name.
ROLE_FILES: dict[PartitionRole, str] = {
    PartitionRole.train: "train.parquet",
    PartitionRole.calibration: "calibration.parquet",
    PartitionRole.selection: "selection.parquet",
    PartitionRole.monitoring: "monitoring.parquet",
}


@dataclass(frozen=True)
class PartitionInfo:
    role: str
    rows: int
    positives: int
    ids_sha256: str
    partition_hash: str


def _coerce_role(role: PartitionRole | str) -> PartitionRole:
    try:
        return PartitionRole(role)
    except ValueError:
        raise LoaderError(
            f"Unknown partition role {role!r}; expected one of {[r.value for r in PartitionRole]}"
        ) from None


def _reject_test(role: PartitionRole) -> None:
    if role is PartitionRole.test:
        raise TestAccessError(
            "The Test partition cannot be read through risklab.data.loaders. It is opened only "
            "by final evaluation with a selection lock (risklab.models.final_eval)."
        )


def _configs(
    data_cfg: DataConfig | None, partition_cfg: PartitionConfig | None, config_dir: Path | None
) -> tuple[DataConfig, PartitionConfig]:
    return (
        data_cfg or load_data_config(config_dir),
        partition_cfg or load_partition_config(config_dir),
    )


def _verified_document(data_cfg: DataConfig, cfg: PartitionConfig) -> dict[str, Any]:
    try:
        return load_verified_document(cfg, data_cfg)
    except PartitionError as exc:
        raise LoaderError(str(exc)) from exc


def partition_info(
    role: PartitionRole | str,
    *,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    config_dir: Path | None = None,
) -> PartitionInfo:
    """Verified size, positives and hashes of a readable role (for logging to MLflow)."""
    r = _coerce_role(role)
    _reject_test(r)
    data_cfg, cfg = _configs(data_cfg, partition_cfg, config_dir)
    doc = _verified_document(data_cfg, cfg)
    stats = doc["roles"][r.value]
    return PartitionInfo(
        role=r.value,
        rows=int(stats["rows"]),
        positives=int(stats["positives"]),
        ids_sha256=str(stats["ids_sha256"]),
        partition_hash=str(doc["partition_hash"]),
    )


def _select_columns(
    columns: Sequence[str] | None, cfg: PartitionConfig, table_columns: Sequence[str]
) -> list[str]:
    """Feature columns to return, validated against the raw schema (id and target excluded)."""
    reserved = {cfg.id_column, cfg.stratify_column}
    if columns is None:
        return [c for c in table_columns if c not in reserved]
    unknown = sorted({c for c in columns if c not in table_columns})
    if unknown:
        raise LoaderError(f"Unknown column(s) for {cfg.source_table}: {unknown}")
    return list(dict.fromkeys(c for c in columns if c not in reserved))


def load_partition(
    role: PartitionRole | str,
    purpose: Purpose | str,
    *,
    columns: Sequence[str] | None = None,
    data_cfg: DataConfig | None = None,
    partition_cfg: PartitionConfig | None = None,
    config_dir: Path | None = None,
) -> pa.Table:
    """Return the id, target and requested application columns of one partition role.

    The table is sorted by the id column. ``columns=None`` returns every column of the source
    table. Raises ``TestAccessError`` for the Test role under any purpose, ``LoaderError`` for a
    role/purpose mismatch or a failed integrity check.
    """
    r = _coerce_role(role)
    _reject_test(r)
    try:
        p = Purpose(purpose)
    except ValueError:
        raise LoaderError(f"Unknown purpose {purpose!r}") from None
    if ALLOWED_PURPOSE[r] is not p:
        raise LoaderError(
            f"Role {r.value!r} may only be read for purpose {ALLOWED_PURPOSE[r].value!r}, "
            f"not {p.value!r}"
        )

    data_cfg, cfg = _configs(data_cfg, partition_cfg, config_dir)
    try:
        schema = load_raw_schema(config_dir, data_cfg=data_cfg)
        table_columns = [c.name for c in schema.columns(cfg.source_table)]
    except ConfigError as exc:
        raise LoaderError(str(exc)) from exc
    feature_cols = _select_columns(columns, cfg, table_columns)

    # Checks the role id hashes, partition_hash and the source Parquet/CSV hashes.
    doc = _verified_document(data_cfg, cfg)
    stats = doc["roles"][r.value]
    parquet = data_cfg.parquet_dir / f"{cfg.source_table}.parquet"
    role_file = cfg.output_dir / ROLE_FILES[r]

    idc, tc = quote_ident(cfg.id_column), quote_ident(cfg.stratify_column)
    select = ", ".join(["s." + idc, "s." + tc, *("s." + quote_ident(c) for c in feature_cols)])
    sql = (
        f"SELECT {select} FROM read_parquet(?) AS s "
        f"JOIN read_parquet(?) AS r ON s.{idc} = r.{idc} ORDER BY s.{idc}"
    )
    con = duckdb_connect(data_cfg.duckdb)
    try:
        table = con.sql(sql, params=[str(parquet), str(role_file)]).to_arrow_table()
    except duckdb.Error as exc:
        raise LoaderError(f"DuckDB error loading role {r.value!r}: {exc}") from exc
    finally:
        con.close()

    _check_table(table, r, stats, cfg)
    return table


def _check_table(
    table: pa.Table, role: PartitionRole, stats: dict[str, Any], cfg: PartitionConfig
) -> None:
    if table.num_rows != stats["rows"]:
        raise LoaderError(
            f"Role {role.value!r} returned {table.num_rows} rows; partitions.json records "
            f"{stats['rows']}. The source table or role file changed."
        )
    if len(pc.unique(table[cfg.id_column])) != table.num_rows:
        raise LoaderError(f"Role {role.value!r} returned duplicate {cfg.id_column} values")
    positives = pc.sum(table[cfg.stratify_column]).as_py() or 0
    if positives != stats["positives"]:
        raise LoaderError(
            f"Role {role.value!r} has {positives} positives; partitions.json records "
            f"{stats['positives']}. The source table changed."
        )


__all__ = [
    "ALLOWED_PURPOSE",
    "LoaderError",
    "PartitionInfo",
    "PartitionRole",
    "Purpose",
    "TestAccessError",
    "load_partition",
    "partition_info",
]
