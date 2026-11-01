"""Deterministic stratified partition assignment (MASTER_PLAN Section 4.1).

Assignment is a pure function of the set of (id, target) pairs, the seed and the shares. It does
not use any RNG stream: within each TARGET class ids are ordered by a BLAKE2b hash of
``"{seed}:{id}"`` (ties broken by id) and cut into roles in the fixed ``PARTITION_ROLES`` order
using exact per-class counts from largest-remainder rounding. Hashes are computed from ids, not
from Parquet bytes, because Parquet output is not byte-reproducible.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from risklab.config import PARTITION_ROLES, DataConfig, PartitionConfig
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import quote_ident
from risklab.manifest import sha256_file, write_manifest

PARTITION_STEP = "partition"
PARTITIONS_FILE = "partitions.json"
DOC_KEY = "_doc"


class PartitionError(Exception):
    """Raised for invalid input data, missing manifests, overwrite conflicts and bad outputs."""


def _blake_key(seed: int, applicant_id: int) -> bytes:
    return hashlib.blake2b(f"{seed}:{applicant_id}".encode(), digest_size=8).digest()


def class_counts(n: int, shares: Mapping[str, float]) -> dict[str, int]:
    """Rows per role for a class of ``n`` ids: largest-remainder rounding of share * n.

    Shares are converted through their decimal string so 0.55 means exactly 55/100. Remainder
    ties are broken by the fixed role order.
    """
    exact = {r: Fraction(str(shares[r])) * n for r in PARTITION_ROLES}
    counts = {r: int(exact[r]) for r in PARTITION_ROLES}  # floor (values are non-negative)
    leftover = n - sum(counts.values())
    if not 0 <= leftover <= len(PARTITION_ROLES):
        raise PartitionError(f"Shares do not sum to 1; cannot allocate {n} rows")
    by_remainder = sorted(
        PARTITION_ROLES, key=lambda r: (-(exact[r] - counts[r]), PARTITION_ROLES.index(r))
    )
    for role in by_remainder[:leftover]:
        counts[role] += 1
    return counts


def assign_partitions(
    rows: Sequence[tuple[int, int]], seed: int, shares: Mapping[str, float]
) -> dict[str, list[int]]:
    """Assign each (id, target) row to a role. Returns sorted id lists per role."""
    by_class: dict[int, list[int]] = {}
    for applicant_id, target in rows:
        by_class.setdefault(target, []).append(applicant_id)
    roles: dict[str, list[int]] = {r: [] for r in PARTITION_ROLES}
    for target in sorted(by_class):
        ordered = sorted(by_class[target], key=lambda i: (_blake_key(seed, i), i))
        counts = class_counts(len(ordered), shares)
        start = 0
        for role in PARTITION_ROLES:
            roles[role].extend(ordered[start : start + counts[role]])
            start += counts[role]
    return {r: sorted(ids) for r, ids in roles.items()}


def ids_sha256(ids: Sequence[int]) -> str:
    """SHA256 of the sorted ids as newline-terminated decimal strings."""
    digest = hashlib.sha256()
    for i in sorted(ids):
        digest.update(f"{i}\n".encode())
    return digest.hexdigest()


def partition_hash(
    seed: int, shares: Mapping[str, float], role_hashes: Mapping[str, str], source_sha256: str
) -> str:
    payload = {
        "seed": seed,
        "shares": dict(shares),
        "role_hashes": dict(role_hashes),
        "source_csv_sha256": source_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_rows(rows: Sequence[tuple[Any, Any]], cfg: PartitionConfig) -> list[tuple[int, int]]:
    """Check ids are unique and non-null and targets are in {0, 1}; return typed rows."""
    idc, tc = cfg.id_column, cfg.stratify_column
    null_ids = sum(1 for i, _ in rows if i is None)
    if null_ids:
        raise PartitionError(f"{cfg.source_table}.{idc} has {null_ids} null value(s)")
    seen: set[Any] = set()
    dupes: set[Any] = set()
    for i, _ in rows:
        (dupes if i in seen else seen).add(i)
    if dupes:
        raise PartitionError(
            f"{cfg.source_table}.{idc} has duplicate ids, e.g. {sorted(dupes)[:5]}"
        )
    null_t = sum(1 for _, t in rows if t is None)
    if null_t:
        raise PartitionError(f"{cfg.source_table}.{tc} has {null_t} null value(s)")
    bad = sorted({t for _, t in rows if t not in (0, 1)})
    if bad:
        raise PartitionError(f"{cfg.source_table}.{tc} must be in {{0, 1}}; found {bad[:5]}")
    return [(int(i), int(t)) for i, t in rows]


def _source_info(data_cfg: DataConfig, cfg: PartitionConfig) -> tuple[str, str, int]:
    """Return (CSV name, raw CSV sha256, expected row count) from the ingest manifest."""
    csv_names = [f for f, t in data_cfg.tables.items() if t == cfg.source_table]
    if not csv_names:
        raise PartitionError(f"source_table {cfg.source_table!r} is not a configured table")
    csv_name = csv_names[0]
    path = data_cfg.manifest_path
    if not path.is_file():
        raise PartitionError(f"Ingest manifest not found: {path}. Run `risklab ingest` first.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        sha = manifest["inputs"][csv_name]
        rows = manifest["summary"]["tables"][cfg.source_table]["parquet_rows"]
        parquet_name = f"{cfg.source_table}.parquet"
        recorded_parquet_sha = manifest["outputs"][parquet_name]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PartitionError(
            f"Ingest manifest {path} has no entry for {csv_name} / {cfg.source_table}: {exc!r}"
            ". Re-run `risklab ingest`."
        ) from exc
    # The CSV hash is only valid provenance if this Parquet is the file ingest produced from it.
    parquet = data_cfg.parquet_dir / parquet_name
    if not parquet.is_file():
        raise PartitionError(f"Parquet file not found: {parquet}. Run `risklab ingest` first.")
    if sha256_file(parquet) != recorded_parquet_sha:
        raise PartitionError(
            f"{parquet} does not match the Parquet hash recorded in {path}; "
            "it is stale or modified. Re-run `risklab ingest`."
        )
    return csv_name, str(sha), int(rows)


def _read_source(
    data_cfg: DataConfig, cfg: PartitionConfig, expected_rows: int
) -> list[tuple[Any, Any]]:
    parquet = data_cfg.parquet_dir / f"{cfg.source_table}.parquet"
    if not parquet.is_file():
        raise PartitionError(f"Parquet file not found: {parquet}. Run `risklab ingest` first.")
    con = duckdb_connect(data_cfg.duckdb)
    try:
        rows = con.execute(
            f"SELECT {quote_ident(cfg.id_column)}, {quote_ident(cfg.stratify_column)} "
            "FROM read_parquet(?)",
            [str(parquet)],
        ).fetchall()
    except duckdb.Error as exc:
        raise PartitionError(f"DuckDB error reading {parquet}: {exc}") from exc
    finally:
        con.close()
    if len(rows) != expected_rows:
        raise PartitionError(
            f"{parquet.name} has {len(rows)} rows but the ingest manifest records {expected_rows}"
        )
    return rows


def _write_role_parquet(path: Path, ids: Sequence[int], id_column: str) -> None:
    pq.write_table(pa.table({id_column: pa.array(ids, type=pa.int64())}), path)


def read_role_ids(path: Path, id_column: str) -> list[int]:
    try:
        return [int(i) for i in pq.read_table(path, columns=[id_column])[id_column].to_pylist()]
    except (OSError, KeyError, pa.ArrowException) as exc:
        raise PartitionError(f"Cannot read partition file {path}: {exc}") from exc


def _read_existing(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise TypeError("top level is not an object")
        doc["partition_hash"]
        doc["roles"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PartitionError(
            f"Existing {path} is unreadable ({exc!r}). Pass --overwrite to replace it."
        ) from exc
    return doc


def verify_outputs(output_dir: Path, doc: Mapping[str, Any], id_column: str) -> None:
    """Check that each role file's ids hash to the value recorded in ``partitions.json``."""
    for role in PARTITION_ROLES:
        path = output_dir / f"{role}.parquet"
        if not path.is_file():
            raise PartitionError(f"Partition file missing: {path}")
        actual = ids_sha256(read_role_ids(path, id_column))
        try:
            expected = doc["roles"][role]["ids_sha256"]
        except (KeyError, TypeError) as exc:
            raise PartitionError(
                f"{PARTITIONS_FILE} has no ids_sha256 for role {role!r} ({exc!r}). "
                "Pass --overwrite to replace it."
            ) from exc
        if actual != expected:
            raise PartitionError(
                f"{path} does not match {PARTITIONS_FILE} (ids hash {actual} != {expected}). "
                "The file was modified or is stale."
            )


def load_verified_document(cfg: PartitionConfig, data_cfg: DataConfig) -> dict[str, Any]:
    """Read ``partitions.json`` and check it against the role files and the ingested source.

    Verifies the role id hashes, that ``partition_hash`` is the value recomputed from the
    document's own seed, shares, role hashes and source hash, and that the recorded source CSV
    hash and Parquet are the ones in the ingest manifest.
    """
    path = cfg.output_dir / PARTITIONS_FILE
    if not path.is_file():
        raise PartitionError(f"{path} not found. Run `risklab partition` first.")
    doc = _read_existing(path)
    verify_outputs(cfg.output_dir, doc, cfg.id_column)
    try:
        role_hashes = {r: doc["roles"][r]["ids_sha256"] for r in PARTITION_ROLES}
        recomputed = partition_hash(
            doc["seed"], doc["shares"], role_hashes, doc["source_csv_sha256"]
        )
    except (KeyError, TypeError) as exc:
        raise PartitionError(f"{path} is incomplete ({exc!r}); pass --overwrite") from exc
    if recomputed != doc["partition_hash"]:
        raise PartitionError(
            f"{path} partition_hash {doc['partition_hash']} != recomputed {recomputed}; "
            "the document was modified."
        )
    _csv, source_sha, _rows = _source_info(data_cfg, cfg)
    if source_sha != doc["source_csv_sha256"]:
        raise PartitionError(
            f"{path} was built from a different source CSV than the current ingest manifest; "
            "re-run `risklab partition --overwrite` only if the data change is intended."
        )
    return doc


def _check_role_sizes(rows: Sequence[tuple[int, int]], roles: Mapping[str, Sequence[int]]) -> None:
    """Every role needs at least one row and one positive; fail before computing rates."""
    if not rows:
        raise PartitionError("Source table has no rows; cannot partition")
    target_of = dict(rows)
    n_pos = sum(target_of.values())
    sizes = f"{n_pos} positive(s), {len(rows) - n_pos} negative(s)"
    for r in PARTITION_ROLES:
        n, pos = len(roles[r]), sum(target_of[i] for i in roles[r])
        if n == 0 or pos == 0:
            raise PartitionError(
                f"Role {r!r} would get {n} row(s) and {pos} positive(s) from {sizes}; "
                "too few rows to partition with these shares"
            )


def _step_manifest_stale(path: Path, p_hash: str) -> bool:
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))["outputs"]["partition_hash"]
        return bool(recorded != p_hash)
    except (OSError, ValueError, KeyError, TypeError):
        return True


def _portable_partition_config(cfg: PartitionConfig, root: Path) -> dict[str, Any]:
    dumped = cfg.model_dump(mode="json")
    p = Path(dumped["output_dir"])
    if p.is_absolute():
        with contextlib.suppress(ValueError):
            dumped["output_dir"] = p.relative_to(root.resolve()).as_posix()
    return dumped


def run_partition(
    cfg: PartitionConfig,
    data_cfg: DataConfig,
    *,
    root: Path,
    overwrite: bool = False,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Assign partitions and write role files, ``partitions.json`` and the step manifest.

    Returns ``{"status": "created" | "overwritten" | "unchanged", "document": {...}}``. All files
    are staged as temp files and replaced only after every one was written and verified; on
    failure the existing outputs are untouched.
    """
    csv_name, source_sha, expected_rows = _source_info(data_cfg, cfg)
    rows = validate_rows(_read_source(data_cfg, cfg, expected_rows), cfg)
    roles = assign_partitions(rows, cfg.seed, cfg.shares)
    target_of = dict(rows)
    _check_role_sizes(rows, roles)

    role_hashes = {r: ids_sha256(roles[r]) for r in PARTITION_ROLES}
    p_hash = partition_hash(cfg.seed, cfg.shares, role_hashes, source_sha)
    total_pos = sum(target_of.values())
    role_stats: dict[str, dict[str, Any]] = {}
    for r in PARTITION_ROLES:
        n, pos = len(roles[r]), sum(target_of[i] for i in roles[r])
        role_stats[r] = {
            "rows": n,
            "positives": pos,
            "positive_rate": pos / n,
            "ids_sha256": role_hashes[r],
        }
    document: dict[str, Any] = {
        "seed": cfg.seed,
        "shares": dict(cfg.shares),
        "source_table": cfg.source_table,
        "source_csv_sha256": source_sha,
        "rows": len(rows),
        "positives": total_pos,
        "positive_rate": total_pos / len(rows),
        "roles": role_stats,
        "partition_hash": p_hash,
    }

    out = cfg.output_dir
    doc_path = out / PARTITIONS_FILE
    manifest_path = data_cfg.reports_dir / "manifests" / f"{PARTITION_STEP}.json"
    status = "created"

    def _write_step_manifest() -> None:
        try:
            write_manifest(
                manifest_path,
                PARTITION_STEP,
                config={"partition": _portable_partition_config(cfg, root)},
                inputs={csv_name: source_sha},
                outputs={
                    **{f"{r}_ids_sha256": role_hashes[r] for r in PARTITION_ROLES},
                    "partition_hash": p_hash,
                },
                summary={
                    "rows": len(rows),
                    "positives": total_pos,
                    "positive_rate": total_pos / len(rows),
                    "roles": {
                        r: {k: v for k, v in role_stats[r].items() if k != "ids_sha256"}
                        for r in PARTITION_ROLES
                    },
                },
                git_cwd=root,
            )
        except OSError as exc:
            raise PartitionError(f"I/O error writing {manifest_path}: {exc}") from exc

    if doc_path.is_file():
        if overwrite:
            status = "overwritten"
        else:
            existing = _read_existing(doc_path)
            if existing["partition_hash"] != p_hash:
                raise PartitionError(
                    f"{doc_path} already exists with a different partition hash "
                    f"({existing['partition_hash']} != {p_hash}). Changing partitions "
                    "invalidates all downstream results (trained models, calibrators, "
                    "selection, Test lock). Pass --overwrite only if that is intended."
                )
            if existing != document:
                raise PartitionError(
                    f"{doc_path} is stale or modified (it differs from the recomputed "
                    "document); pass --overwrite"
                )
            verify_outputs(out, document, cfg.id_column)
            refreshed = _step_manifest_stale(manifest_path, p_hash)
            if refreshed:
                _write_step_manifest()
            status = "unchanged (step manifest refreshed)" if refreshed else "unchanged"
            log(f"Partitions {status} (hash {p_hash})")
            return {"status": status, "document": document}
    else:
        stray = [r for r in PARTITION_ROLES if (out / f"{r}.parquet").exists()]
        if stray and not overwrite:
            raise PartitionError(
                f"{doc_path} is missing but {stray} exist in {out}; pass --overwrite to replace"
            )
        if stray:
            status = "overwritten"

    final_paths = {r: out / f"{r}.parquet" for r in PARTITION_ROLES}
    final_paths[DOC_KEY] = doc_path
    staged = {k: p.with_name(f".{p.name}.tmp") for k, p in final_paths.items()}
    try:
        out.mkdir(parents=True, exist_ok=True)
        for r in PARTITION_ROLES:
            _write_role_parquet(staged[r], roles[r], cfg.id_column)
            if ids_sha256(read_role_ids(staged[r], cfg.id_column)) != role_hashes[r]:
                raise PartitionError(f"Verification of staged file for role {r!r} failed")
        staged[DOC_KEY].write_text(
            json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        # The document is replaced last so a crash between replaces is caught by verify_outputs.
        for key in (*PARTITION_ROLES, DOC_KEY):
            os.replace(staged[key], final_paths[key])
    except OSError as exc:
        raise PartitionError(f"I/O error writing partitions: {exc}") from exc
    finally:
        for tmp in staged.values():
            tmp.unlink(missing_ok=True)

    _write_step_manifest()
    log(f"Partitions {status} (hash {p_hash})")
    return {"status": status, "document": document}


__all__ = [
    "PARTITION_STEP",
    "PartitionError",
    "assign_partitions",
    "class_counts",
    "ids_sha256",
    "partition_hash",
    "read_role_ids",
    "load_verified_document",
    "run_partition",
    "validate_rows",
    "verify_outputs",
]
