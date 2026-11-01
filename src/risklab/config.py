"""YAML configuration loading with pydantic validation and path resolution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from risklab.manifest import config_hash

CONFIG_DIR_ENV = "RISKLAB_CONFIG_DIR"
DEFAULT_CONFIG_DIR = "configs"


class ConfigError(Exception):
    """Raised when a configuration file is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


MEMORY_LIMIT_PATTERN = re.compile(r"\d+(\.\d+)?\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)", re.IGNORECASE)


class DuckDBConfig(_Strict):
    memory_limit: str = "4GB"

    @field_validator("memory_limit")
    @classmethod
    def _memory_limit_format(cls, v: str) -> str:
        if not MEMORY_LIMIT_PATTERN.fullmatch(v.strip()):
            raise ValueError(f"invalid memory_limit {v!r}; expected e.g. '4GB' or '512MiB'")
        return v

    temp_directory: Path
    threads: int | None = Field(default=None, ge=1)
    preserve_insertion_order: bool = False


class SyntheticConfig(_Strict):
    out_dir: Path = Path("data/synthetic")
    n_applicants: int = Field(default=2000, ge=50)
    seed: int = 20260101


class DataConfig(_Strict):
    raw_dir: Path
    parquet_dir: Path
    manifest_path: Path
    reports_dir: Path = Path("reports")
    tables: dict[str, str]
    duckdb: DuckDBConfig
    synthetic: SyntheticConfig = SyntheticConfig()

    @field_validator("tables")
    @classmethod
    def _tables_valid(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("tables must not be empty")
        names = list(v.values())
        if len(set(names)) != len(names):
            raise ValueError("table names must be unique")
        return v

    def resolved(self, root: Path) -> DataConfig:
        """Return a copy with relative paths made absolute against ``root``."""

        def r(p: Path) -> Path:
            return p if p.is_absolute() else root / p

        return self.model_copy(
            update={
                "raw_dir": r(self.raw_dir),
                "parquet_dir": r(self.parquet_dir),
                "manifest_path": r(self.manifest_path),
                "reports_dir": r(self.reports_dir),
                "synthetic": self.synthetic.model_copy(
                    update={"out_dir": r(self.synthetic.out_dir)}
                ),
                "duckdb": self.duckdb.model_copy(
                    update={"temp_directory": r(self.duckdb.temp_directory)}
                ),
            }
        )


PARTITION_ROLES = ("train", "calibration", "selection", "test", "monitoring")
SHARE_TOLERANCE = 1e-9


class PartitionConfig(_Strict):
    """Partition assignment settings (``partition.yaml``)."""

    seed: int
    shares: dict[str, float]
    source_table: str
    id_column: str
    stratify_column: str
    output_dir: Path

    @field_validator("shares")
    @classmethod
    def _shares_valid(cls, v: dict[str, float]) -> dict[str, float]:
        missing, extra = set(PARTITION_ROLES) - set(v), set(v) - set(PARTITION_ROLES)
        if missing or extra:
            raise ValueError(
                f"shares must have exactly the roles {list(PARTITION_ROLES)} "
                f"(missing: {sorted(missing)}, unexpected: {sorted(extra)})"
            )
        bad = sorted(k for k, share in v.items() if not share > 0)
        if bad:
            raise ValueError(f"shares must be > 0 (got non-positive for {bad})")
        total = sum(v.values())
        if abs(total - 1.0) > SHARE_TOLERANCE:
            raise ValueError(f"shares must sum to 1 (got {total!r})")
        return v

    def resolved(self, root: Path) -> PartitionConfig:
        out = self.output_dir
        return self.model_copy(update={"output_dir": out if out.is_absolute() else root / out})


ALLOWED_DUCKDB_TYPES = frozenset(
    {"BIGINT", "INTEGER", "SMALLINT", "TINYINT", "DOUBLE", "FLOAT", "BOOLEAN", "DATE", "VARCHAR"}
)


class ColumnSpec(_Strict):
    name: str = Field(min_length=1)
    type: str

    @field_validator("type")
    @classmethod
    def _type_allowed(cls, v: str) -> str:
        if v not in ALLOWED_DUCKDB_TYPES:
            raise ValueError(f"unsupported type {v!r}; allowed: {sorted(ALLOWED_DUCKDB_TYPES)}")
        return v


class RawSchema(_Strict):
    """Ordered columns with explicit DuckDB types for every raw table (the ingest contract)."""

    tables: dict[str, list[ColumnSpec]]

    @field_validator("tables")
    @classmethod
    def _columns_valid(cls, v: dict[str, list[ColumnSpec]]) -> dict[str, list[ColumnSpec]]:
        for table, cols in v.items():
            if not cols:
                raise ValueError(f"table {table!r} has no columns")
            seen: set[str] = set()
            for col in cols:
                key = col.name.lower()  # DuckDB identifiers are case-insensitive
                if key in seen:
                    raise ValueError(f"table {table!r}: duplicate column {col.name!r}")
                seen.add(key)
        return v

    def columns(self, table: str) -> list[ColumnSpec]:
        try:
            return self.tables[table]
        except KeyError:
            raise ConfigError(f"Table {table!r} is not defined in raw_schema.yaml") from None

    def table_hash(self, table: str) -> str:
        """Stable SHA256 of one table's schema entry (ordered names and types)."""
        return config_hash({"columns": [c.model_dump() for c in self.columns(table)]})


def resolve_config_dir(config_dir: Path | str | None = None) -> Path:
    """Pick the config directory: explicit argument, then env var, then ``./configs``."""
    chosen = config_dir or os.environ.get(CONFIG_DIR_ENV) or DEFAULT_CONFIG_DIR
    return Path(chosen).expanduser().resolve()


def project_root(config_dir: Path) -> Path:
    """The project root is the directory containing the config directory."""
    return config_dir.parent


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config file {path} must contain a YAML mapping")
    return loaded


def load_data_config(config_dir: Path | str | None = None) -> DataConfig:
    """Load ``data.yaml`` and resolve its relative paths against the project root."""
    cfg_dir = resolve_config_dir(config_dir)
    path = cfg_dir / "data.yaml"
    raw = _load_yaml(path)
    try:
        cfg = DataConfig.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"Invalid config {path}: {details}") from exc
    return cfg.resolved(project_root(cfg_dir))


def load_raw_schema(
    config_dir: Path | str | None = None, *, data_cfg: DataConfig | None = None
) -> RawSchema:
    """Load ``raw_schema.yaml``; every configured table must be defined and none may be extra."""
    cfg_dir = resolve_config_dir(config_dir)
    path = cfg_dir / "raw_schema.yaml"
    raw = _load_yaml(path)
    try:
        schema = RawSchema.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"Invalid config {path}: {details}") from exc
    if data_cfg is not None:
        expected = set(data_cfg.tables.values())
        missing, extra = expected - set(schema.tables), set(schema.tables) - expected
        if missing or extra:
            raise ConfigError(
                f"{path} does not match data.yaml tables (missing: {sorted(missing)}, "
                f"extra: {sorted(extra)})"
            )
    return schema


def load_partition_config(config_dir: Path | str | None = None) -> PartitionConfig:
    """Load ``partition.yaml`` and resolve ``output_dir`` against the project root."""
    cfg_dir = resolve_config_dir(config_dir)
    path = cfg_dir / "partition.yaml"
    raw = _load_yaml(path)
    try:
        cfg = PartitionConfig.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"Invalid config {path}: {details}") from exc
    return cfg.resolved(project_root(cfg_dir))


def load_model[ModelT: BaseModel](
    config_dir: Path | str | None, filename: str, model: type[ModelT]
) -> ModelT:
    """Load ``<config_dir>/<filename>`` and validate it against a pydantic model."""
    path = resolve_config_dir(config_dir) / filename
    raw = _load_yaml(path)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"Invalid config {path}: {details}") from exc
