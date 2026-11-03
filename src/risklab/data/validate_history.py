"""Historical-table validation: FK coverage, temporal validity, duplicate keys.

Counts only; no applicant-level statistic is computed from TARGET. Orphan monthly rows
keyed on SK_ID_CURR are still usable by the feature SQL, while orphan bureau_balance rows
(no SK_ID_CURR) can never be attributed and are only counted here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb

from risklab.config import DataConfig
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import quote_str


class HistoryValidationError(Exception):
    """Raised when the Parquet inputs are missing."""


def validate_history(data_cfg: DataConfig, *, config_dir: Path | None = None) -> dict[str, Any]:
    """Validate keys and log the exact exclusions used by shared historical cleaning."""
    from risklab.config import load_model, load_raw_schema
    from risklab.features.history_clean import HistoricalConfig, install_history_views
    from risklab.manifest import build_manifest, sha256_file, write_json_atomic

    config_dir = config_dir or data_cfg.reports_dir.parent / "configs"
    cfg = load_model(config_dir, "historical.yaml", HistoricalConfig)
    root = config_dir.parent
    query_text = (root / cfg.validation_sql).read_text(encoding="utf-8")
    queries = {}
    for block in query_text.split("-- name:")[1:]:
        name, sql = block.strip().split("\n", 1)
        queries[name.strip()] = sql.strip().replace(
            "{installment_amount_tolerance}", str(cfg.installment_amount_tolerance)
        )
    if not set(cfg.error_checks) <= set(queries):
        raise HistoryValidationError("unknown configured historical error checks")
    con = duckdb_connect(data_cfg.duckdb)
    try:
        for table in cfg.tables:
            path = data_cfg.parquet_dir / f"{table}.parquet"
            if not path.is_file():
                raise HistoryValidationError(f"Parquet file missing: {path}")
            con.execute(
                f"CREATE VIEW {table} AS SELECT * FROM read_parquet({quote_str(str(path))})"
            )
            con.execute(f"CREATE VIEW raw_{table} AS SELECT * FROM {table}")
        counts = {name: int(con.sql(sql).fetchall()[0][0]) for name, sql in queries.items()}
        raw_counts = {
            table: int(con.sql(f"SELECT COUNT(*) FROM {table}").fetchall()[0][0])
            for table in cfg.tables
        }
        # Preserve raw views as direct Parquet scans before replacing the public names.
        for table in cfg.tables:
            path = data_cfg.parquet_dir / f"{table}.parquet"
            con.execute(
                f"CREATE OR REPLACE VIEW raw_{table} AS SELECT * "
                f"FROM read_parquet({quote_str(str(path))})"
            )
        install_history_views(con, config_dir=config_dir, schema=load_raw_schema(config_dir))
        excluded = {
            table: raw_counts[table]
            - int(con.sql(f"SELECT COUNT(*) FROM {table}").fetchall()[0][0])
            for table in cfg.tables
        }
        exclusion_reasons = {}
        sentinel_counts = {}
        for table, rules in cfg.tables.items():
            for column in rules.non_future_columns:
                exclusion_reasons[f"{table}.{column}.future"] = int(
                    con.sql(f"SELECT COUNT(*) FROM raw_{table} WHERE {column} > 0").fetchall()[0][0]
                )
            for column in rules.required_event_columns:
                exclusion_reasons[f"{table}.{column}.missing"] = int(
                    con.sql(f"SELECT COUNT(*) FROM raw_{table} WHERE {column} IS NULL").fetchall()[
                        0
                    ][0]
                )
            for column in rules.sentinel_columns:
                sentinel_counts[f"{table}.{column}"] = int(
                    con.sql(
                        f"SELECT COUNT(*) FROM raw_{table} WHERE {column} = ?",
                        params=[cfg.sentinel_value],
                    ).fetchall()[0][0]
                )
    except duckdb.Error as exc:
        raise HistoryValidationError(f"DuckDB history validation failed: {exc}") from exc
    finally:
        con.close()
    errors = [name for name in cfg.error_checks if counts[name]]
    report = {
        "counts": counts,
        "excluded_rows": excluded,
        "exclusion_reasons": exclusion_reasons,
        "exclusion_reason_counts_overlap": True,
        "sentinels_to_null": sentinel_counts,
        "errors": errors,
        "passed": not errors,
        "temporal_valid": not any(excluded.values()),
        "status": "failed"
        if errors
        else ("passed_with_exclusions" if any(excluded.values()) else "passed"),
    }
    write_json_atomic(data_cfg.reports_dir / "validation/history.json", report)
    manifest = build_manifest(
        "validate-history",
        config=cfg.model_dump(),
        inputs={"data_manifest": sha256_file(data_cfg.manifest_path)},
        summary=report,
        git_cwd=root,
    )
    write_json_atomic(data_cfg.reports_dir / "manifests/validate-history.json", manifest)
    return report


def sql_files_reference_target(sql_dir: Path) -> list[str]:
    """Files under ``sql_dir`` mentioning TARGET (must be empty for feature SQL)."""
    hits: list[str] = []
    for path in sorted(sql_dir.rglob("*.sql")):
        if "TARGET" in path.read_text(encoding="utf-8"):
            hits.append(str(path))
    return hits


__all__ = ["HistoryValidationError", "sql_files_reference_target", "validate_history"]


def ensure_history_validated(data_cfg: DataConfig, config_dir: Path) -> None:
    """Verify one immutable input snapshot once per process, before any history feature build."""
    import json

    from risklab.manifest import sha256_file

    document = json.loads(data_cfg.manifest_path.read_text())
    paths = [
        data_cfg.manifest_path,
        config_dir / "historical.yaml",
        config_dir.parent / "sql/validation/history.sql",
    ]
    paths += [data_cfg.parquet_dir / name for name in document["outputs"]]
    paths += [data_cfg.raw_dir / name for name in document["inputs"]]
    fingerprint = tuple(
        (str(path), path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
        for path in paths
    )
    _validated_snapshot(config_dir, fingerprint, sha256_file(data_cfg.manifest_path))


@lru_cache(maxsize=8)
def _validated_snapshot(config_dir: Path, fingerprint: tuple, manifest_hash: str) -> None:
    import json

    from risklab.config import load_data_config
    from risklab.manifest import sha256_file

    cfg = load_data_config(config_dir)
    document = json.loads(cfg.manifest_path.read_text())
    for section, directory in (("inputs", cfg.raw_dir), ("outputs", cfg.parquet_dir)):
        for name, expected in document[section].items():
            if sha256_file(directory / name) != expected:
                raise HistoryValidationError(f"{name} no longer matches ingest; run ingest again")
    report = validate_history(cfg, config_dir=config_dir)
    if not report["passed"]:
        raise HistoryValidationError(f"historical key conflicts: {report['errors']}")
