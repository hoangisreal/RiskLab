"""Draft raw-data anomaly counts over the ingested Parquet files (feeds docs/data_contract.md).

Runs ``sql/validation/raw_anomalies.sql`` plus generated per-column scans (sentinel 365243,
'XNA', sign and range of time fields). Writes ``reports/validation/raw_anomalies.json``
(gitignored) and prints a summary.

Usage: uv run python scripts/raw_anomalies.py
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from risklab.config import load_data_config, load_raw_schema, project_root, resolve_config_dir
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import quote_ident, quote_str
from risklab.manifest import write_json_atomic

SENTINEL = 365243


def _blocks(text: str) -> dict[str, str]:
    parts = re.split(r"^-- name:\s*(\w+)\s*$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default=None)
    args = parser.parse_args()
    cfg_dir = resolve_config_dir(args.config_dir)
    root = project_root(cfg_dir)
    cfg = load_data_config(cfg_dir)
    schema = load_raw_schema(cfg_dir, data_cfg=cfg)

    con = duckdb_connect(cfg.duckdb)
    for table in cfg.tables.values():
        path = cfg.parquet_dir / f"{table}.parquet"
        con.execute(
            f"CREATE VIEW {quote_ident(table)} AS "
            f"SELECT * FROM read_parquet({quote_str(str(path))})"
        )
    con.execute(
        "CREATE VIEW applications AS "
        "SELECT SK_ID_CURR FROM application_train UNION ALL SELECT SK_ID_CURR FROM application_test"
    )

    results: dict[str, Any] = {}
    sql_text = (root / "sql" / "validation" / "raw_anomalies.sql").read_text(encoding="utf-8")
    for name, sql in _blocks(sql_text).items():
        results[name] = {m: v for m, v in con.execute(sql.rstrip(";")).fetchall()}

    sentinel: dict[str, int] = {}
    xna: dict[str, int] = {}
    time_fields: dict[str, dict[str, float | int | None]] = {}
    for table in cfg.tables.values():
        cols = schema.columns(table)
        exprs: list[str] = []
        for c in cols:
            q = quote_ident(c.name)
            if c.name.startswith("DAYS_"):
                exprs.append(f"count(*) FILTER (WHERE {q} = {SENTINEL})")
                exprs.append(f"count(*) FILTER (WHERE {q} > 0 AND {q} <> {SENTINEL})")
                exprs.append(f"count(*) FILTER (WHERE {q} > 0)")
                exprs.append(f"min({q}) FILTER (WHERE {q} <> {SENTINEL})")
                exprs.append(f"max({q}) FILTER (WHERE {q} <> {SENTINEL})")
            elif c.name == "MONTHS_BALANCE":
                exprs.append("0")
                exprs.append(f"count(*) FILTER (WHERE {q} > 0)")
                exprs.append(f"count(*) FILTER (WHERE {q} >= 0)")
                exprs.append(f"min({q})")
                exprs.append(f"max({q})")
            elif c.type == "VARCHAR":
                exprs.append(f"count(*) FILTER (WHERE {q} = 'XNA')")
        row = con.execute(f"SELECT {', '.join(exprs)} FROM {quote_ident(table)}").fetchone()
        assert row is not None
        i = 0
        for c in cols:
            key = f"{table}.{c.name}"
            if c.name.startswith("DAYS_") or c.name == "MONTHS_BALANCE":
                s, pos_ex, pos_all, lo, hi = row[i : i + 5]
                i += 5
                if c.name.startswith("DAYS_") and s:
                    sentinel[key] = s
                time_fields[key] = {
                    "sentinel_365243": s,
                    "positive_excluding_sentinel": pos_ex,
                    "positive_or_zero_for_MONTHS_BALANCE": pos_all,
                    "min_excluding_sentinel": lo,
                    "max_excluding_sentinel": hi,
                }
            elif c.type == "VARCHAR":
                if row[i]:
                    xna[key] = row[i]
                i += 1
    results["sentinel_365243"] = sentinel
    results["xna"] = xna
    results["time_fields"] = time_fields

    out = write_json_atomic(cfg.reports_dir / "validation" / "raw_anomalies.json", results)
    print(json.dumps(results, indent=1, default=str))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
