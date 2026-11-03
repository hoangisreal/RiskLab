"""Batch feature build: run the shared SQL groups over Parquet with DuckDB.

The same SQL files drive the online path (``features/online.py``); only the views differ
(Parquet files here, in-memory request tables there). No statistic across applicants is
computed in SQL: each query aggregates only one applicant's own history rows.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa

from risklab.config import DataConfig, load_raw_schema
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import quote_str
from risklab.data.validate_history import ensure_history_validated
from risklab.features.history_clean import install_history_views
from risklab.features.registry import FeatureRegistry, load_registry
from risklab.manifest import sha256_file, write_manifest

FEATURES_STEP = "features"


class FeatureBuildError(Exception):
    """Raised when a feature group cannot be built."""


def _sql_for_group(group: str, registry: FeatureRegistry, root: Path) -> str:
    path = root / registry.groups[group].source_sql
    if not path.is_file():
        raise FeatureBuildError(f"feature SQL not found: {path}")
    return path.read_text(encoding="utf-8")


def run_group_sql(con: duckdb.DuckDBPyConnection, sql: str) -> pa.Table:
    return con.sql(sql).to_arrow_table()


def build_group_frames(
    con: duckdb.DuckDBPyConnection,
    groups: Sequence[str],
    registry: FeatureRegistry,
    root: Path,
) -> dict[str, pd.DataFrame]:
    """Execute each group's SQL on the open connection (views must already exist)."""
    frames: dict[str, pd.DataFrame] = {}
    for group in groups:
        table = run_group_sql(con, _sql_for_group(group, registry, root))
        frame = table.to_pandas()
        expected = ["SK_ID_CURR"] + [f.name for f in registry.groups[group].features]
        missing = [c for c in expected if c not in frame.columns]
        if missing:
            raise FeatureBuildError(f"group {group!r} SQL missed columns {missing}")
        frames[group] = frame.set_index("SK_ID_CURR").sort_index()
    return frames


def open_batch_connection(
    data_cfg: DataConfig,
    applicant_ids: Sequence[int],
    application_cleaned: pa.Table,
    config_dir: Path,
) -> duckdb.DuckDBPyConnection:
    """Connection with Parquet views plus the cleaned application and applicant views."""
    ensure_history_validated(data_cfg, config_dir)
    con = duckdb_connect(data_cfg.duckdb)
    parquet = data_cfg.parquet_dir
    for table in (
        "bureau",
        "bureau_balance",
        "previous_application",
        "pos_cash_balance",
        "credit_card_balance",
        "installments_payments",
    ):
        path = parquet / f"{table}.parquet"
        if not path.is_file():
            raise FeatureBuildError(f"Parquet file not found: {path}. Run `risklab ingest` first.")
        con.execute(
            f"CREATE OR REPLACE VIEW raw_{table} AS SELECT * "
            f"FROM read_parquet({quote_str(str(path))})"
        )
    install_history_views(con, config_dir=config_dir, schema=load_raw_schema(config_dir))
    con.register("application_src", application_cleaned)
    con.register(
        "applicants", pa.table({"SK_ID_CURR": pa.array(list(applicant_ids), type=pa.int64())})
    )
    return con


def build_features(
    *,
    data_cfg: DataConfig,
    root: Path,
    applicant_ids: Sequence[int],
    application_cleaned: pa.Table,
    groups: Sequence[str] | None = None,
    registry: FeatureRegistry | None = None,
    output_dir: Path | None = None,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict[str, pd.DataFrame]:
    """Build the requested groups for ``applicant_ids``; optionally write per-group Parquet."""
    registry = registry or load_registry(root / "configs")
    groups = list(groups) if groups is not None else registry.all_groups()
    con = open_batch_connection(data_cfg, applicant_ids, application_cleaned, root / "configs")
    try:
        frames = build_group_frames(con, groups, registry, root)
    finally:
        con.close()
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for group, frame in frames.items():
            frame.reset_index().to_parquet(output_dir / f"{group}.parquet", index=False)
            log(f"features/{group}.parquet: {len(frame)} rows, {frame.shape[1]} features")
        manifest = write_manifest(
            data_cfg.reports_dir / "manifests/features.json",
            FEATURES_STEP,
            config=registry.model_dump(),
            inputs={"data_manifest": sha256_file(data_cfg.manifest_path)},
            outputs={g: sha256_file(output_dir / f"{g}.parquet") for g in groups},
            summary={
                "contract_version": registry.contract_version,
                "groups": {g: sorted(frames[g].columns.tolist()) for g in groups},
                "rows": int(len(applicant_ids)),
            },
            git_cwd=root,
        )
        from risklab.manifest import write_json_atomic

        write_json_atomic(output_dir / "features_manifest.json", manifest)
    return frames


def combine_frames(frames: dict[str, pd.DataFrame], groups: Sequence[str]) -> pd.DataFrame:
    """Join group frames (already indexed by SK_ID_CURR) into one feature matrix."""
    cols: list[pd.DataFrame] = []
    for g in groups:
        if g not in frames:
            raise FeatureBuildError(f"missing built group {g!r}")
        cols.append(frames[g])
    if not cols:
        return pd.DataFrame()
    return pd.concat(cols, axis=1).sort_index()


__all__ = [
    "FEATURES_STEP",
    "FeatureBuildError",
    "build_features",
    "build_group_frames",
    "combine_frames",
    "open_batch_connection",
    "run_group_sql",
]
