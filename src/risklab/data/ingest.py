"""Raw CSV -> Parquet ingest with an explicit schema and strict parsing (MASTER_PLAN Section 8).

Design notes:

* Header check first: every selected CSV must match ``raw_schema.yaml`` exactly (names and order).
* CSVs are read by DuckDB as all-VARCHAR with an explicit ``columns=`` mapping (no sniffing) and
  cast in SQL. The CSV reader's own typed parsing silently rounds ``'2.5'`` to ``3`` for integer
  columns, so integer columns are additionally guarded by a full-match regex. Any value that
  cannot be cast raises an error naming the table, column and value instead of becoming NULL.
* The row count check counts CSV records with a second DuckDB pass (all-VARCHAR, no casts). It
  handles quoted newlines because it uses the same CSV parser, but it does not depend on type
  conversion or on the Parquet writer. A raw ``wc -l`` would miscount quoted newlines.
"""

from __future__ import annotations

import csv
import json
import os
import resource
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

from risklab.config import ColumnSpec, DataConfig, RawSchema
from risklab.data.duckdb_utils import duckdb_connect
from risklab.manifest import sha256_file, write_json_atomic, write_manifest

INGEST_STEP = "ingest"
INTEGER_TYPES = frozenset({"BIGINT", "INTEGER", "SMALLINT", "TINYINT"})
FLOAT_TYPES = frozenset({"DOUBLE", "FLOAT"})
INTEGER_PATTERN = "[+-]?[0-9]+"


class IngestError(Exception):
    """Raised for missing files, schema mismatches, parse failures and failed verification."""


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def read_header(path: Path) -> list[str]:
    """First record of a CSV file (``utf-8-sig`` so a BOM does not leak into the first name)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        try:
            return next(csv.reader(fh))
        except StopIteration:
            return []


def header_mismatch(expected: list[str], actual: list[str]) -> str | None:
    """Describe how ``actual`` differs from ``expected``; ``None`` when identical."""
    if actual == expected:
        return None
    missing = [c for c in expected if c not in actual]
    extra = [c for c in actual if c not in expected]
    parts = []
    if missing:
        parts.append(f"missing columns: {missing}")
    if extra:
        parts.append(f"unexpected columns: {extra}")
    dupes = sorted({c for c in actual if actual.count(c) > 1})
    if dupes:
        parts.append(f"duplicated columns: {dupes}")
    if not parts:
        moved = [
            f"position {i}: expected {e!r}, found {a!r}"
            for i, (e, a) in enumerate(zip(expected, actual, strict=True))
            if e != a
        ]
        parts.append(
            "columns are reordered ("
            + "; ".join(moved[:5])
            + ("; ..." if len(moved) > 5 else "")
            + ")"
        )
    return "; ".join(parts)


def _columns_struct(cols: list[ColumnSpec]) -> str:
    """DuckDB struct literal mapping every column to VARCHAR (types are applied by casts)."""
    return "{" + ", ".join(f"{quote_str(c.name)}: 'VARCHAR'" for c in cols) + "}"


def _read_csv_sql(cols: list[ColumnSpec]) -> str:
    return (
        "read_csv(?, header=true, auto_detect=false, "
        f"columns={_columns_struct(cols)}, nullstr='', "
        "ignore_errors=false, null_padding=false)"
    )


def cast_expression(table: str, col: ColumnSpec) -> str:
    """SQL expression that casts a VARCHAR column strictly, raising on anything non-conforming."""
    c = quote_ident(col.name)
    if col.type == "VARCHAR":
        return f"{c} AS {c}"
    msg = quote_str(f"{table}.{col.name}: cannot parse as {col.type}: ")
    cast = f"CAST({c} AS {col.type})"
    try_cast = f"TRY_CAST({c} AS {col.type})"
    if col.type in INTEGER_TYPES:
        ok = f"regexp_full_match({c}, {quote_str(INTEGER_PATTERN)}) AND {try_cast} IS NOT NULL"
    elif col.type in FLOAT_TYPES:
        ok = f"{try_cast} IS NOT NULL AND isfinite({try_cast})"
    else:
        ok = f"{try_cast} IS NOT NULL"
    return (
        f"CASE WHEN {c} IS NULL THEN NULL WHEN {ok} THEN {cast} ELSE error({msg} || {c}) END AS {c}"
    )


def count_csv_records(con: duckdb.DuckDBPyConnection, path: Path, cols: list[ColumnSpec]) -> int:
    row = con.execute(f"SELECT count(*) FROM {_read_csv_sql(cols)}", [str(path)]).fetchone()
    assert row is not None
    return int(row[0])


def peak_rss_bytes() -> int:
    """Peak resident set size of this process (``ru_maxrss`` is KiB on Linux)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _convert(
    con: duckdb.DuckDBPyConnection, table: str, csv_path: Path, cols: list[ColumnSpec], out: Path
) -> None:
    select = ", ".join(cast_expression(table, c) for c in cols)
    sql = (
        f"COPY (SELECT {select} FROM {_read_csv_sql(cols)}) TO {quote_str(str(out))} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    try:
        con.execute(sql, [str(csv_path)])
    except duckdb.Error as exc:
        raise IngestError(f"{table}: failed to parse {csv_path.name}: {exc}") from exc


def _verify_parquet(
    con: duckdb.DuckDBPyConnection, table: str, path: Path, cols: list[ColumnSpec], csv_rows: int
) -> int:
    desc = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
    actual = [(r[0], r[1]) for r in desc]
    expected = [(c.name, c.type) for c in cols]
    if actual != expected:
        raise IngestError(f"{table}: Parquet schema differs from raw_schema.yaml: {actual[:3]}...")
    row = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()
    assert row is not None
    rows = int(row[0])
    if rows != csv_rows:
        raise IngestError(f"{table}: Parquet has {rows} rows but the CSV has {csv_rows} data rows")
    return rows


def preflight(cfg: DataConfig, schema: RawSchema, tables: list[str]) -> None:
    """Check raw files exist and headers match the schema, reporting every problem at once."""
    problems: list[str] = []
    files = {t: cfg.raw_dir / f for f, t in cfg.tables.items()}
    missing = [str(files[t]) for t in tables if not files[t].is_file()]
    if missing:
        problems.append("Missing raw files:\n  " + "\n  ".join(missing))
    for t in tables:
        if not files[t].is_file():
            continue
        expected = [c.name for c in schema.columns(t)]
        diff = header_mismatch(expected, read_header(files[t]))
        if diff:
            problems.append(f"{t} ({files[t].name}): header does not match raw_schema.yaml: {diff}")
    if problems:
        raise IngestError("\n".join(problems))


def _previous_manifest(path: Path) -> dict[str, Any]:
    """Previous ingest manifest; ``{}`` when absent. An unreadable one is an error."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IngestError(
            f"Existing manifest {path} is unreadable ({exc}); fix or remove it, then re-ingest"
        ) from exc
    if not isinstance(loaded, dict):
        raise IngestError(f"Existing manifest {path} is not a JSON object; fix or remove it")
    return loaded


def _portable_config(cfg: DataConfig, root: Path) -> dict[str, Any]:
    """Config as a dict with paths relative to ``root`` (outside paths stay absolute)."""

    def rel(value: str) -> str:
        p = Path(value)
        if p.is_absolute():
            try:
                return p.relative_to(root.resolve()).as_posix()
            except ValueError:
                return value
        return value

    dumped = cfg.model_dump(mode="json")
    for key in ("raw_dir", "parquet_dir", "manifest_path", "reports_dir"):
        dumped[key] = rel(dumped[key])
    dumped["synthetic"]["out_dir"] = rel(dumped["synthetic"]["out_dir"])
    dumped["duckdb"]["temp_directory"] = rel(dumped["duckdb"]["temp_directory"])
    return dumped


def _carry_forward(
    cfg: DataConfig,
    schema: RawSchema,
    previous: dict[str, Any],
    configured: list[str],
    selected: list[str],
    csv_names: dict[str, str],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any], list[str]]:
    """Validate and collect manifest entries of tables that are not re-ingested in this run."""
    prev_tables = (previous.get("summary") or {}).get("tables") or {}
    prev_inputs = previous.get("inputs") or {}
    prev_outputs = previous.get("outputs") or {}
    inputs: dict[str, str] = {}
    outputs: dict[str, str] = {}
    summaries: dict[str, Any] = {}
    missing: list[str] = []
    problems: list[str] = []
    for t in configured:
        if t in selected:
            continue
        entry = prev_tables.get(t)
        if entry is None:
            missing.append(t)
            continue
        out_name = f"{t}.parquet"
        parquet = cfg.parquet_dir / out_name
        recorded = prev_outputs.get(out_name)
        if not isinstance(entry, dict) or entry.get("schema_hash") != schema.table_hash(t):
            problems.append(f"{t}: raw_schema.yaml changed since it was ingested")
        elif not parquet.is_file():
            problems.append(f"{t}: {parquet} is missing")
        elif not recorded or sha256_file(parquet) != recorded:
            problems.append(f"{t}: {parquet} does not match the hash recorded in the manifest")
        else:
            inputs[csv_names[t]] = prev_inputs.get(csv_names[t], "")
            outputs[out_name] = recorded
            summaries[t] = entry
    if problems:
        raise IngestError(
            "Cannot keep manifest entries for tables not selected in this run; re-ingest them "
            "(risklab ingest --table T, or without --table):\n  " + "\n  ".join(problems)
        )
    return inputs, outputs, summaries, missing


def _convert_all(
    cfg: DataConfig,
    schema: RawSchema,
    selected: list[str],
    csv_names: dict[str, str],
    staged: dict[str, tuple[Path, Path]],
    log: Callable[[str], None],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Phase 1: convert and verify every selected table into ``.tmp`` files (nothing replaced)."""
    summaries: dict[str, dict[str, Any]] = {}
    try:
        con = duckdb_connect(cfg.duckdb)
    except (duckdb.Error, OSError) as exc:
        raise IngestError(f"Cannot start DuckDB: {exc}") from exc
    try:
        settings = {
            name: con.execute(f"SELECT current_setting('{name}')").fetchone()
            for name in ("memory_limit", "threads", "temp_directory", "preserve_insertion_order")
        }
        for t in selected:
            t0 = time.monotonic()
            cols = schema.columns(t)
            csv_path = cfg.raw_dir / csv_names[t]
            tmp = staged[t][0]
            tmp.unlink(missing_ok=True)
            _convert(con, t, csv_path, cols, tmp)
            csv_rows = count_csv_records(con, csv_path, cols)
            rows = _verify_parquet(con, t, tmp, cols, csv_rows)
            summaries[t] = {
                "raw_bytes": csv_path.stat().st_size,
                "parquet_bytes": tmp.stat().st_size,
                "csv_rows": csv_rows,
                "parquet_rows": rows,
                "columns": len(cols),
                "schema_hash": schema.table_hash(t),
            }
            log(f"{t}: {rows:,} rows, {len(cols)} columns, {time.monotonic() - t0:.1f}s")
    except duckdb.Error as exc:
        raise IngestError(f"DuckDB error: {exc}") from exc
    except OSError as exc:
        raise IngestError(f"I/O error during ingest: {exc}") from exc
    finally:
        con.close()
    return summaries, {k: (v[0] if v else None) for k, v in settings.items()}


def run_ingest(
    cfg: DataConfig,
    schema: RawSchema,
    *,
    root: Path,
    tables: list[str] | None = None,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Ingest the selected tables (default all) and write both manifests. Returns the manifest.

    All selected tables are converted and verified into temp files first. Only when every table
    passes are they moved into place and the manifest written; on any failure the temp files are
    removed and existing Parquet files and manifest stay untouched.
    """
    configured = list(cfg.tables.values())
    selected = tables or configured
    unknown = sorted(set(selected) - set(configured))
    if unknown:
        raise IngestError(f"Unknown table(s) {unknown}; configured tables: {configured}")
    selected = [t for t in configured if t in set(selected)]
    start = time.monotonic()
    preflight(cfg, schema, selected)

    csv_names = {t: f for f, t in cfg.tables.items()}
    previous = _previous_manifest(cfg.manifest_path)
    inputs, outputs, table_summary, missing = _carry_forward(
        cfg, schema, previous, configured, selected, csv_names
    )

    staged = {
        t: (cfg.parquet_dir / f".{t}.parquet.tmp", cfg.parquet_dir / f"{t}.parquet")
        for t in selected
    }
    try:
        cfg.parquet_dir.mkdir(parents=True, exist_ok=True)
        new_summaries, effective = _convert_all(cfg, schema, selected, csv_names, staged, log)
        new_inputs = {csv_names[t]: sha256_file(cfg.raw_dir / csv_names[t]) for t in selected}
        new_outputs = {final.name: sha256_file(tmp) for tmp, final in staged.values()}
        for tmp, final in staged.values():
            os.replace(tmp, final)
    except OSError as exc:
        raise IngestError(f"I/O error during ingest: {exc}") from exc
    finally:
        for tmp, _final in staged.values():
            tmp.unlink(missing_ok=True)

    inputs.update(new_inputs)
    outputs.update(new_outputs)
    table_summary.update(new_summaries)
    peak_rss = peak_rss_bytes()
    wall = round(time.monotonic() - start, 1)
    for t in selected:
        table_summary[t]["run"] = {
            "peak_rss_bytes": peak_rss,
            "wall_seconds": wall,
            "duckdb_version": duckdb.__version__,
        }

    summary = {
        "tables": dict(sorted(table_summary.items())),
        "tables_missing": missing,
        "duckdb": {
            "version": duckdb.__version__,
            "configured": _portable_config(cfg, root)["duckdb"],
            "effective": effective,
        },
        "peak_rss_bytes": peak_rss,
        "wall_seconds": wall,
        "tables_ingested_this_run": selected,
    }
    config = {
        "data": _portable_config(cfg, root),
        "schema_hashes": {t: schema.table_hash(t) for t in configured},
    }
    manifest = write_manifest(
        cfg.manifest_path,
        INGEST_STEP,
        config=config,
        inputs=inputs,
        outputs=outputs,
        summary=summary,
        git_cwd=root,
    )
    write_json_atomic(cfg.reports_dir / "manifests" / f"{INGEST_STEP}.json", manifest)
    return manifest
