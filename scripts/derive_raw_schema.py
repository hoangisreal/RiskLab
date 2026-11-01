"""Dev helper: sniff the full raw CSVs with DuckDB and write a draft ``raw_schema.yaml``.

The output is a starting point for manual review. ``risklab ingest`` never calls this script;
``configs/raw_schema.yaml`` is the contract.

Usage: uv run python scripts/derive_raw_schema.py [--out configs/raw_schema.yaml]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from risklab.config import load_data_config, project_root, resolve_config_dir
from risklab.data.duckdb_utils import duckdb_connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    cfg_dir = resolve_config_dir(args.config_dir)
    cfg = load_data_config(cfg_dir)
    out = args.out or project_root(cfg_dir) / "configs" / "raw_schema.yaml"

    lines = [
        "# Raw CSV schema: ordered columns with explicit DuckDB types. This file is the contract.",
        "# Draft from scripts/derive_raw_schema.py (full-file sniff), then reviewed.",
        "tables:",
    ]
    con = duckdb_connect(cfg.duckdb)
    for csv_name, table in cfg.tables.items():
        path = cfg.raw_dir / csv_name
        rows = con.execute(
            "DESCRIBE SELECT * FROM read_csv(?, header=true, sample_size=-1)", [str(path)]
        ).fetchall()
        print(f"{table}: {len(rows)} columns")
        lines.append(f"  {table}:")
        for name, dtype, *_ in rows:
            lines.append(f"    - {{name: {name}, type: {dtype}}}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
