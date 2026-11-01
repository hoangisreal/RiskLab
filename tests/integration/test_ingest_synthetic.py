import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risklab.cli import app
from risklab.config import DataConfig, RawSchema
from risklab.data.duckdb_utils import duckdb_connect
from tests.conftest import write_config_dir
from tests.fixtures.synthetic import generate

pytestmark = pytest.mark.integration


def test_synthetic_to_parquet_via_cli(
    tmp_path: Path,
    make_config: Callable[..., DataConfig],
    raw_schema: RawSchema,
    repo_data_config: DataConfig,
) -> None:
    cfg = make_config()
    names = {t: f for f, t in cfg.tables.items()}
    counts = generate(cfg.raw_dir, raw_schema, names, n_applicants=300, seed=3)
    cfg_dir = write_config_dir(tmp_path, cfg, raw_schema)

    result = CliRunner().invoke(app, ["--config-dir", str(cfg_dir), "ingest"])
    assert result.exit_code == 0, result.output

    manifest = json.loads(cfg.manifest_path.read_text())
    assert manifest == json.loads((cfg.reports_dir / "manifests" / "ingest.json").read_text())
    con = duckdb_connect(cfg.duckdb)
    for table in cfg.tables.values():
        pq = cfg.parquet_dir / f"{table}.parquet"
        csv_data_rows = len((cfg.raw_dir / names[table]).read_text().splitlines()) - 1
        assert csv_data_rows == counts[table]
        n_rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(pq)]).fetchone()
        assert n_rows is not None and n_rows[0] == counts[table]
        described = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(pq)]).fetchall()
        assert [(r[0], r[1]) for r in described] == [
            (c.name, c.type) for c in raw_schema.columns(table)
        ]
        entry = manifest["summary"]["tables"][table]
        assert entry["parquet_rows"] == entry["csv_rows"] == counts[table]
        assert entry["columns"] == len(raw_schema.columns(table))
    assert len(manifest["inputs"]) == len(manifest["outputs"]) == len(cfg.tables)

    # Subset re-run keeps the manifest complete.
    again = CliRunner().invoke(app, ["--config-dir", str(cfg_dir), "ingest", "--table", "bureau"])
    assert again.exit_code == 0, again.output
    assert len(json.loads(cfg.manifest_path.read_text())["summary"]["tables"]) == len(cfg.tables)
