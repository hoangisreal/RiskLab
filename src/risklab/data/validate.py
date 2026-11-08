"""Validation of the raw application tables.

Design decision: validation is SQL-driven over the ingested Parquet files with DuckDB, not
Pandera/pandas, so no new dependency is needed and nothing is loaded into memory. The SQL is
generated from the rules in ``application_validation.yaml`` and the categorical domains in
``application_domains.yaml``; the only inline SQL is the structural checks (keys, row counts,
TARGET domain). It runs on the full ``application_train`` and ``application_test`` tables, not on
partitions. TARGET is only checked for presence, nulls and its {0, 1} domain; no statistic is
computed from it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import Field, model_validator

from risklab.config import (
    ConfigError,
    DataConfig,
    RawSchema,
    _Strict,
)
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import quote_ident, quote_str
from risklab.manifest import config_hash, sha256_file, write_json_atomic, write_manifest

VALIDATE_STEP = "validate"
TRAIN = "application_train"
TEST = "application_test"
ID_COLUMN = "SK_ID_CURR"
TARGET_COLUMN = "TARGET"
TARGET_VALUES = (0, 1)
MAX_EXAMPLES = 5
TOP_NULL_COLUMNS = 10
REPORT_NAME = "application.json"

ERROR, WARNING, INFO = "error", "warning", "info"


class ValidateError(Exception):
    """Raised when validation cannot run (missing manifest, stale Parquet, unreadable table)."""


class SentinelSpec(_Strict):
    column: str
    value: int


class RangeRule(_Strict):
    """Numeric rule. Select columns with ``columns`` or ``prefix``; NULL never violates."""

    name: str
    columns: list[str] | None = None
    prefix: str | None = None
    gt: float | None = Field(default=None, allow_inf_nan=False)
    ge: float | None = Field(default=None, allow_inf_nan=False)
    lt: float | None = Field(default=None, allow_inf_nan=False)
    le: float | None = Field(default=None, allow_inf_nan=False)
    values: list[float] | None = None
    allowed: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> RangeRule:
        if (self.columns is None) == (self.prefix is None):
            raise ValueError(f"rule {self.name!r}: set exactly one of columns, prefix")
        has_bound = any(b is not None for b in (self.gt, self.ge, self.lt, self.le))
        if has_bound == (self.values is not None):
            raise ValueError(f"rule {self.name!r}: set bounds (gt/ge/lt/le) or values, not both")
        for v in [*(self.values or []), *self.allowed]:
            if v != v or v in (float("inf"), float("-inf")):
                raise ValueError(f"rule {self.name!r}: values must be finite")
        return self


class KnownException(_Strict):
    """Rows of one table accepted as violations of one rule check, matched by ``SK_ID_CURR``."""

    table: Literal["application_train", "application_test"]
    check: str
    ids: list[int] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> KnownException:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError(f"known exception {self.table}/{self.check}: ids must be unique")
        if not self.reason.strip():
            raise ValueError(f"known exception {self.table}/{self.check}: reason is blank")
        return self


class ValidationRules(_Strict):
    sentinels: list[SentinelSpec] = Field(default_factory=list)
    xna_value: str = "XNA"
    ranges: list[RangeRule]
    known_exceptions: list[KnownException] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_exceptions(self) -> ValidationRules:
        keys = [(e.table, e.check) for e in self.known_exceptions]
        if len(set(keys)) != len(keys):
            raise ValueError("known_exceptions: at most one entry per (table, check)")
        return self


class ApplicationDomains(_Strict):
    domains: dict[str, list[str]]


def _num(value: float) -> str:
    return repr(float(value))


def resolve_rule_columns(rule: RangeRule, schema: RawSchema) -> list[str]:
    """Columns a rule applies to, checked against the application_train schema."""
    types = {c.name: c.type for c in schema.columns(TRAIN)}
    if rule.prefix is not None:
        cols = [c for c, t in types.items() if c.startswith(rule.prefix) and t != "VARCHAR"]
        if not cols:
            raise ConfigError(f"rule {rule.name!r}: no numeric column starts with {rule.prefix!r}")
        return cols
    assert rule.columns is not None
    for c in rule.columns:
        if c not in types:
            raise ConfigError(f"rule {rule.name!r}: unknown column {c!r} in {TRAIN}")
        if types[c] == "VARCHAR":
            raise ConfigError(f"rule {rule.name!r}: column {c!r} is VARCHAR, not numeric")
    return list(rule.columns)


def check_rules_against_schema(
    rules: ValidationRules, domains: ApplicationDomains, schema: RawSchema
) -> None:
    """Fail on rules for unknown columns and on VARCHAR columns without a domain."""
    types = {c.name: c.type for c in schema.columns(TRAIN)}
    for rule in rules.ranges:
        resolve_rule_columns(rule, schema)
    for s in rules.sentinels:
        if s.column not in types:
            raise ConfigError(f"sentinel column {s.column!r} is not in {TRAIN}")
    excepting = {"categorical_domain", *(f"range:{r.name}" for r in rules.ranges)}
    for e in rules.known_exceptions:
        if e.check not in excepting:
            raise ConfigError(
                f"known exception for {e.table}: {e.check!r} is not a rule check "
                f"(one of {sorted(excepting)})"
            )
    varchar = {c for c, t in types.items() if t == "VARCHAR"}
    unknown = sorted(set(domains.domains) - varchar)
    missing = sorted(varchar - set(domains.domains))
    if unknown or missing:
        raise ConfigError(
            f"application_domains.yaml must cover exactly the VARCHAR columns of {TRAIN} "
            f"(unknown: {unknown}, missing: {missing})"
        )


def _range_predicate(col: str, rule: RangeRule) -> str:
    q = quote_ident(col)
    if rule.values is not None:
        ok = ", ".join(_num(v) for v in rule.values)
        cond = f"{q} IN ({ok})"
    else:
        parts = [
            f"{q} {op} {_num(bound)}"
            for op, bound in ((">", rule.gt), (">=", rule.ge), ("<", rule.lt), ("<=", rule.le))
            if bound is not None
        ]
        cond = " AND ".join(parts)
        if rule.allowed:
            cond = f"({cond}) OR {q} IN ({', '.join(_num(v) for v in rule.allowed)})"
    return f"{q} IS NOT NULL AND NOT ({cond})"


def _domain_predicate(col: str, values: list[str]) -> str:
    q = quote_ident(col)
    return f"{q} IS NOT NULL AND {q} NOT IN ({', '.join(quote_str(v) for v in values)})"


def _violations(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    table: str,
    severity: str,
    relation: str,
    preds: dict[str, str],
    details_note: dict[str, Any] | None = None,
    exception: KnownException | None = None,
) -> dict[str, Any]:
    """Count rows matching any predicate, per-predicate counts, and a few example ids.

    With ``exception``, offending rows whose id is listed are reported under
    ``accepted_exceptions`` and excluded from the counts and examples.
    """
    accepted: dict[str, Any] | None = None
    if exception is not None:
        idq = quote_ident(ID_COLUMN)
        listed = ", ".join(str(int(i)) for i in exception.ids)
        full = " OR ".join(f"({p})" for p in preds.values()) or "FALSE"
        matched = con.execute(
            f"SELECT {idq}, count(*) FROM {relation} WHERE ({full}) AND {idq} IN ({listed}) "
            f"GROUP BY {idq} ORDER BY {idq}"
        ).fetchall()
        accepted = {
            "ids": [int(r[0]) for r in matched],
            "count": sum(int(r[1]) for r in matched),
            "reason": exception.reason,
        }
        not_listed = f"NOT COALESCE({idq} IN ({listed}), FALSE)"
        preds = {c: f"({p}) AND {not_listed}" for c, p in preds.items()}
    any_pred = " OR ".join(f"({p})" for p in preds.values()) or "FALSE"
    counts_sql = ", ".join(f"count(*) FILTER (WHERE {p})" for p in preds.values())
    row = con.execute(
        f"SELECT count(*) FILTER (WHERE {any_pred})"
        + (f", {counts_sql}" if preds else "")
        + f" FROM {relation}"
    ).fetchone()
    assert row is not None
    total, per = int(row[0]), [int(x) for x in row[1:]]
    examples: list[int] = []
    if total:
        idq = quote_ident(ID_COLUMN)
        examples = [
            int(r[0])
            for r in con.execute(
                f"SELECT DISTINCT {idq} FROM {relation} WHERE ({any_pred}) AND {idq} IS NOT NULL "
                f"ORDER BY {idq} LIMIT {MAX_EXAMPLES}"
            ).fetchall()
        ]
    check: dict[str, Any] = {
        "name": name,
        "table": table,
        "severity": severity,
        "offending_rows": total,
        "examples": examples,
    }
    details: dict[str, Any] = dict(details_note or {})
    by_column = {c: n for c, n in zip(preds, per, strict=True) if n}
    if len(preds) > 1 and by_column:
        details["by_column"] = by_column
    if details:
        check["details"] = details
    if accepted is not None and accepted["count"]:
        check["accepted_exceptions"] = accepted
        check["status"] = "failed" if total else "passed_with_exceptions"
    return check


def _read_sources(data_cfg: DataConfig) -> dict[str, dict[str, Any]]:
    """Ingest-manifest entries for both tables; the Parquet files must match their hashes."""
    path = data_cfg.manifest_path
    if not path.is_file():
        raise ValidateError(f"Ingest manifest not found: {path}. Run `risklab ingest` first.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidateError(f"Ingest manifest {path} is unreadable: {exc!r}") from exc
    csv_of = {t: f for f, t in data_cfg.tables.items()}
    sources: dict[str, dict[str, Any]] = {}
    for table in (TRAIN, TEST):
        parquet = data_cfg.parquet_dir / f"{table}.parquet"
        try:
            csv = csv_of[table]
            entry = {
                "csv": csv,
                "csv_sha256": manifest["inputs"][csv],
                "parquet_sha256": manifest["outputs"][f"{table}.parquet"],
                "rows": int(manifest["summary"]["tables"][table]["parquet_rows"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidateError(
                f"Ingest manifest {path} has no entry for {table}: {exc!r}. "
                "Re-run `risklab ingest`."
            ) from exc
        if not parquet.is_file():
            raise ValidateError(f"Parquet file not found: {parquet}. Run `risklab ingest` first.")
        if sha256_file(parquet) != entry["parquet_sha256"]:
            raise ValidateError(
                f"{parquet} does not match the Parquet hash recorded in {path}; "
                "it is stale or modified. Re-run `risklab ingest`."
            )
        sources[table] = entry
    return sources


def _table_columns(con: duckdb.DuckDBPyConnection, view: str) -> set[str]:
    return {r[0] for r in con.execute(f"DESCRIBE {quote_ident(view)}").fetchall()}


def _structural_checks(
    con: duckdb.DuckDBPyConnection, sources: dict[str, dict[str, Any]], test_cols: set[str]
) -> list[dict[str, Any]]:
    idq = quote_ident(ID_COLUMN)
    tq = quote_ident(TARGET_COLUMN)
    out: list[dict[str, Any]] = []
    for table in (TRAIN, TEST):
        t = quote_ident(table)
        out.append(
            _violations(
                con,
                name="id_null",
                table=table,
                severity=ERROR,
                relation=t,
                preds={ID_COLUMN: f"{idq} IS NULL"},
            )
        )
        out.append(
            _violations(
                con,
                name="id_duplicate",
                table=table,
                severity=ERROR,
                relation=f"(SELECT {idq}, count(*) OVER (PARTITION BY {idq}) AS _n FROM {t})",
                preds={ID_COLUMN: f"{idq} IS NOT NULL AND _n > 1"},
            )
        )
    out.append(
        _violations(
            con,
            name="id_overlap_train_test",
            table=TEST,
            severity=ERROR,
            relation=quote_ident(TEST),
            preds={ID_COLUMN: f"{idq} IN (SELECT {idq} FROM {quote_ident(TRAIN)})"},
        )
    )
    for table in (TRAIN, TEST):
        row = con.execute(f"SELECT count(*) FROM {quote_ident(table)}").fetchone()
        assert row is not None
        actual, expected = int(row[0]), sources[table]["rows"]
        out.append(
            {
                "name": "row_count_matches_manifest",
                "table": table,
                "severity": ERROR,
                "offending_rows": abs(actual - expected),
                "examples": [],
                "details": {"expected": expected, "actual": actual},
            }
        )
    train = quote_ident(TRAIN)
    out.append(
        _violations(
            con,
            name="target_null",
            table=TRAIN,
            severity=ERROR,
            relation=train,
            preds={TARGET_COLUMN: f"{tq} IS NULL"},
        )
    )
    allowed = ", ".join(str(v) for v in TARGET_VALUES)
    out.append(
        _violations(
            con,
            name="target_domain",
            table=TRAIN,
            severity=ERROR,
            relation=train,
            preds={TARGET_COLUMN: f"{tq} IS NOT NULL AND {tq} NOT IN ({allowed})"},
        )
    )
    test_rows = sources[TEST]["rows"]
    out.append(
        {
            "name": "target_absent_in_test",
            "table": TEST,
            "severity": ERROR,
            "offending_rows": test_rows if TARGET_COLUMN in test_cols else 0,
            "examples": [],
        }
    )
    return out


def _rule_checks(
    con: duckdb.DuckDBPyConnection,
    schema: RawSchema,
    rules: ValidationRules,
    domains: ApplicationDomains,
    columns: dict[str, set[str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    known: dict[tuple[str, str], KnownException] = {
        (e.table, e.check): e for e in rules.known_exceptions
    }

    def run(table: str, name: str, preds: dict[str, str]) -> None:
        exception = known.get((table, name))
        check = _violations(
            con,
            name=name,
            table=table,
            severity=ERROR,
            relation=quote_ident(table),
            preds=preds,
            exception=exception,
        )
        out.append(check)
        if exception is not None and "accepted_exceptions" not in check:
            out.append(
                {
                    "name": "stale_known_exception",
                    "table": table,
                    "severity": WARNING,
                    "offending_rows": len(exception.ids),
                    "examples": exception.ids[:MAX_EXAMPLES],
                    "details": {"check": name, "reason": exception.reason},
                }
            )

    for table in (TRAIN, TEST):
        present = columns[table]
        run(
            table,
            "categorical_domain",
            {c: _domain_predicate(c, v) for c, v in domains.domains.items() if c in present},
        )
        for rule in rules.ranges:
            run(
                table,
                f"range:{rule.name}",
                {
                    c: _range_predicate(c, rule)
                    for c in resolve_rule_columns(rule, schema)
                    if c in present
                },
            )
    return out


def _info_checks(
    con: duckdb.DuckDBPyConnection,
    schema: RawSchema,
    rules: ValidationRules,
    columns: dict[str, set[str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    varchar = [c.name for c in schema.columns(TRAIN) if c.type == "VARCHAR"]
    for table in (TRAIN, TEST):
        t = quote_ident(table)
        present = columns[table]
        for s in rules.sentinels:
            if s.column in present:
                out.append(
                    _violations(
                        con,
                        name=f"sentinel_count:{s.column}",
                        table=table,
                        severity=INFO,
                        relation=t,
                        preds={s.column: f"{quote_ident(s.column)} = {int(s.value)}"},
                        details_note={"value": s.value},
                    )
                )
        xna_preds = {
            c: f"{quote_ident(c)} = {quote_str(rules.xna_value)}" for c in varchar if c in present
        }
        out.append(
            _violations(
                con,
                name="xna_count",
                table=table,
                severity=INFO,
                relation=t,
                preds=xna_preds,
                details_note={"value": rules.xna_value},
            )
        )
        cols = sorted(present)
        row = con.execute(
            "SELECT count(*), " + ", ".join(f"count({quote_ident(c)})" for c in cols) + f" FROM {t}"
        ).fetchone()
        assert row is not None
        n = int(row[0])
        rates = {c: (n - int(k)) / n for c, k in zip(cols, row[1:], strict=True) if int(k) < n}
        top = sorted(rates.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_NULL_COLUMNS]
        out.append(
            {
                "name": "null_rates",
                "table": table,
                "severity": INFO,
                "offending_rows": 0,
                "examples": [],
                "details": {
                    "columns_with_nulls": len(rates),
                    "top": [{"column": c, "null_rate": r} for c, r in top],
                },
            }
        )
    return out


def run_validate(
    data_cfg: DataConfig,
    schema: RawSchema,
    rules: ValidationRules,
    domains: ApplicationDomains,
    *,
    root: Path,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Validate both application tables; write the JSON report and the step manifest.

    Returns the report document. ``status`` is ``failed`` if any error-severity check has
    offending rows; the caller decides the exit code.
    """
    check_rules_against_schema(rules, domains, schema)
    sources = _read_sources(data_cfg)
    con = duckdb_connect(data_cfg.duckdb)
    try:
        for table in (TRAIN, TEST):
            path = data_cfg.parquet_dir / f"{table}.parquet"
            con.execute(
                f"CREATE VIEW {quote_ident(table)} AS "
                f"SELECT * FROM read_parquet({quote_str(str(path))})"
            )
        columns = {t: _table_columns(con, t) for t in (TRAIN, TEST)}
        expected = {c.name for c in schema.columns(TRAIN)}
        for table, required in ((TRAIN, expected), (TEST, expected - {TARGET_COLUMN})):
            missing = sorted(required - columns[table])
            if missing:
                raise ValidateError(f"{table} is missing columns from raw_schema.yaml: {missing}")
        log("Running application checks")
        checks = [
            *_structural_checks(con, sources, columns[TEST]),
            *_rule_checks(con, schema, rules, domains, columns),
            *_info_checks(con, schema, rules, columns),
        ]
    except duckdb.Error as exc:
        raise ValidateError(f"DuckDB error during validation: {exc}") from exc
    finally:
        con.close()

    errors = [c for c in checks if c["severity"] == ERROR and c["offending_rows"] > 0]
    cfg_hash = config_hash(
        {"rules": rules.model_dump(mode="json"), "domains": domains.model_dump(mode="json")}
    )
    document: dict[str, Any] = {
        "status": "failed" if errors else "passed",
        "config_hash": cfg_hash,
        "sources": sources,
        "summary": {
            "checks": len(checks),
            "errors": len(errors),
            "warnings": sum(
                1 for c in checks if c["severity"] == WARNING and c["offending_rows"] > 0
            ),
            "accepted_exceptions": sum(1 for c in checks if "accepted_exceptions" in c),
        },
        "checks": checks,
    }
    report_path = data_cfg.reports_dir / "validation" / REPORT_NAME
    manifest_path = data_cfg.reports_dir / "manifests" / f"{VALIDATE_STEP}.json"
    try:
        write_json_atomic(report_path, document)
        write_manifest(
            manifest_path,
            VALIDATE_STEP,
            config={
                "rules": rules.model_dump(mode="json"),
                "domains": domains.model_dump(mode="json"),
            },
            inputs={
                **{s["csv"]: s["csv_sha256"] for s in sources.values()},
                **{f"{t}.parquet": s["parquet_sha256"] for t, s in sources.items()},
            },
            outputs={REPORT_NAME: sha256_file(report_path)},
            summary={"status": document["status"], **document["summary"]},
            git_cwd=root,
        )
    except OSError as exc:
        raise ValidateError(f"I/O error writing validation outputs: {exc}") from exc
    return document


__all__ = [
    "ApplicationDomains",
    "KnownException",
    "RangeRule",
    "ValidateError",
    "ValidationRules",
    "check_rules_against_schema",
    "run_validate",
]
