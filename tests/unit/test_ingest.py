import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risklab.cli import app
from risklab.config import ConfigError, DataConfig, RawSchema, load_data_config
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import (
    IngestError,
    _verify_parquet,
    count_csv_records,
    header_mismatch,
    run_ingest,
)
from risklab.manifest import sha256_file
from tests.conftest import write_config_dir

pytestmark = pytest.mark.unit

MINI = RawSchema.model_validate(
    {
        "tables": {
            "t": [
                {"name": "ID", "type": "BIGINT"},
                {"name": "X", "type": "DOUBLE"},
                {"name": "N", "type": "INTEGER"},
                {"name": "S", "type": "VARCHAR"},
            ],
            "u": [{"name": "ID", "type": "BIGINT"}],
        }
    }
)
GOOD = "ID,X,N,S\n1,1.5,2,a\n2,,3,\n3,-4.0,5,c\n"
Configure = Callable[..., DataConfig]


@pytest.fixture
def cfg(make_config: Configure) -> DataConfig:
    c = make_config({"t.csv": "t", "u.csv": "u"})
    c.raw_dir.mkdir(parents=True)
    (c.raw_dir / "t.csv").write_text(GOOD, encoding="utf-8")
    (c.raw_dir / "u.csv").write_text("ID\n10\n20\n", encoding="utf-8")
    return c


def test_header_mismatch_reports_missing_extra_reordered() -> None:
    exp = ["A", "B", "C"]
    assert header_mismatch(exp, exp) is None
    msg = header_mismatch(exp, ["A", "C", "D"])
    assert (
        msg is not None and "missing columns: ['B']" in msg and "unexpected columns: ['D']" in msg
    )
    msg = header_mismatch(exp, ["A", "C", "B"])
    assert msg is not None and "reordered" in msg and "position 1" in msg


def test_ingest_writes_parquet_and_manifests(cfg: DataConfig, tmp_path: Path) -> None:
    manifest = run_ingest(cfg, MINI, root=tmp_path)
    con = duckdb_connect(cfg.duckdb)
    rows = con.execute(
        f"SELECT ID, X, N, S FROM read_parquet('{cfg.parquet_dir / 't.parquet'}') ORDER BY ID"
    ).fetchall()
    assert rows == [(1, 1.5, 2, "a"), (2, None, 3, None), (3, -4.0, 5, "c")]
    assert not list(cfg.parquet_dir.glob(".*.tmp"))

    on_disk = json.loads(cfg.manifest_path.read_text())
    step = json.loads((cfg.reports_dir / "manifests" / "ingest.json").read_text())
    assert on_disk == step == json.loads(json.dumps(manifest))
    assert on_disk["step"] == "ingest"
    assert on_disk["inputs"]["t.csv"] == sha256_file(cfg.raw_dir / "t.csv")
    assert on_disk["outputs"]["t.parquet"] == sha256_file(cfg.parquet_dir / "t.parquet")
    t = on_disk["summary"]["tables"]["t"]
    assert (t["csv_rows"], t["parquet_rows"], t["columns"]) == (3, 3, 4)
    assert t["raw_bytes"] == (cfg.raw_dir / "t.csv").stat().st_size
    assert t["schema_hash"] == MINI.table_hash("t")
    assert on_disk["summary"]["peak_rss_bytes"] > 0
    assert on_disk["summary"]["duckdb"]["effective"]["memory_limit"]


def test_rerun_overwrites_and_subset_keeps_other_entries(cfg: DataConfig, tmp_path: Path) -> None:
    run_ingest(cfg, MINI, root=tmp_path)
    (cfg.raw_dir / "u.csv").write_text("ID\n10\n20\n30\n", encoding="utf-8")
    manifest = run_ingest(cfg, MINI, root=tmp_path, tables=["u"])
    tables = manifest["summary"]["tables"]
    assert tables["u"]["parquet_rows"] == 3
    assert tables["t"]["parquet_rows"] == 3
    assert manifest["inputs"]["t.csv"] == sha256_file(cfg.raw_dir / "t.csv")
    assert manifest["summary"]["tables_ingested_this_run"] == ["u"]


def test_unknown_table_rejected(cfg: DataConfig, tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="Unknown table"):
        run_ingest(cfg, MINI, root=tmp_path, tables=["nope"])


def test_missing_files_listed(cfg: DataConfig, tmp_path: Path) -> None:
    (cfg.raw_dir / "t.csv").unlink()
    (cfg.raw_dir / "u.csv").unlink()
    with pytest.raises(IngestError) as err:
        run_ingest(cfg, MINI, root=tmp_path)
    assert "t.csv" in str(err.value) and "u.csv" in str(err.value)


def test_wrong_header_fails_before_writing(cfg: DataConfig, tmp_path: Path) -> None:
    (cfg.raw_dir / "t.csv").write_text("ID,N,X,S,EXTRA\n1,2,3,a,b\n", encoding="utf-8")
    with pytest.raises(IngestError, match="unexpected columns: \\['EXTRA'\\]"):
        run_ingest(cfg, MINI, root=tmp_path)
    assert not cfg.parquet_dir.exists() or not list(cfg.parquet_dir.iterdir())
    assert not cfg.manifest_path.exists()


@pytest.mark.parametrize(
    ("row", "column"),
    [
        ("1,abc,2,a", "t.X"),  # non-numeric in DOUBLE
        ("1,1.5,2.5,a", "t.N"),  # fractional value in INTEGER would be rounded by a plain cast
        ("1,1.5,3000000000,a", "t.N"),  # overflow
        ("1.0,1.5,2,a", "t.ID"),  # decimal-formatted value in BIGINT
        ("1,nan,2,a", "t.X"),  # non-finite DOUBLE
        ("1,inf,2,a", "t.X"),
        ("1,-inf,2,a", "t.X"),
    ],
)
def test_strict_cast_failure_names_column_and_keeps_old_output(
    cfg: DataConfig, tmp_path: Path, row: str, column: str
) -> None:
    run_ingest(cfg, MINI, root=tmp_path)
    before = sha256_file(cfg.parquet_dir / "t.parquet")
    (cfg.raw_dir / "t.csv").write_text(f"ID,X,N,S\n{row}\n", encoding="utf-8")
    with pytest.raises(IngestError, match=column.replace(".", "\\.")):
        run_ingest(cfg, MINI, root=tmp_path, tables=["t"])
    assert sha256_file(cfg.parquet_dir / "t.parquet") == before
    assert not list(cfg.parquet_dir.glob(".*.tmp"))


def test_row_count_handles_quoted_newlines(cfg: DataConfig) -> None:
    path = cfg.raw_dir / "t.csv"
    path.write_text('ID,X,N,S\n1,1.0,1,"two\nlines"\n2,2.0,2,b\n', encoding="utf-8")
    assert len(path.read_text().splitlines()) - 1 == 3  # wc -l style count is wrong
    con = duckdb_connect(cfg.duckdb)
    assert count_csv_records(con, path, MINI.columns("t")) == 2


def test_row_count_mismatch_detected(cfg: DataConfig, tmp_path: Path) -> None:
    run_ingest(cfg, MINI, root=tmp_path)
    con = duckdb_connect(cfg.duckdb)
    with pytest.raises(IngestError, match="3 rows but the CSV has 4"):
        _verify_parquet(con, "t", cfg.parquet_dir / "t.parquet", MINI.columns("t"), 4)


def test_cli_wrong_header_and_bad_value_exit_nonzero(cfg: DataConfig, tmp_path: Path) -> None:
    cfg_dir = write_config_dir(tmp_path, cfg, MINI)
    runner = CliRunner()
    args = ["--config-dir", str(cfg_dir), "ingest"]
    assert runner.invoke(app, args).exit_code == 0

    (cfg.raw_dir / "t.csv").write_text("ID,X,S,N\n1,1.0,a,2\n", encoding="utf-8")
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "reordered" in result.output and "raw_schema.yaml" in result.output

    (cfg.raw_dir / "t.csv").write_text("ID,X,N,S\n1,oops,2,a\n", encoding="utf-8")
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "t.X: cannot parse as DOUBLE: oops" in result.output


def test_failure_in_later_table_leaves_outputs_and_manifest_untouched(
    cfg: DataConfig, tmp_path: Path
) -> None:
    run_ingest(cfg, MINI, root=tmp_path)
    files = [cfg.parquet_dir / "t.parquet", cfg.parquet_dir / "u.parquet", cfg.manifest_path]
    before = [f.read_bytes() for f in files]
    (cfg.raw_dir / "t.csv").write_text("ID,X,N,S\n7,1.5,2,new\n", encoding="utf-8")
    (cfg.raw_dir / "u.csv").write_text("ID\nnot_a_number\n", encoding="utf-8")
    with pytest.raises(IngestError, match=r"u\.ID"):
        run_ingest(cfg, MINI, root=tmp_path)
    assert [f.read_bytes() for f in files] == before
    assert not list(cfg.parquet_dir.glob(".*.tmp"))


def test_partial_ingest_fails_when_other_table_schema_changed(
    cfg: DataConfig, tmp_path: Path
) -> None:
    run_ingest(cfg, MINI, root=tmp_path)
    changed = RawSchema.model_validate(
        {
            "tables": {
                "t": [{"name": "ID", "type": "BIGINT"}, {"name": "X", "type": "DOUBLE"}]
                + [{"name": "N", "type": "INTEGER"}, {"name": "S", "type": "VARCHAR"}],
                "u": [{"name": "ID", "type": "INTEGER"}],
            }
        }
    )
    with pytest.raises(IngestError, match=r"(?s)re-ingest.*\n\s+u: ") as err:
        run_ingest(cfg, changed, root=tmp_path, tables=["t"])
    assert "t: " not in str(err.value).split("re-ingest", 1)[1]


def test_partial_ingest_fails_when_other_parquet_tampered(cfg: DataConfig, tmp_path: Path) -> None:
    run_ingest(cfg, MINI, root=tmp_path)
    with open(cfg.parquet_dir / "u.parquet", "ab") as fh:
        fh.write(b"x")
    with pytest.raises(IngestError, match=r"u: .*does not match"):
        run_ingest(cfg, MINI, root=tmp_path, tables=["t"])


def test_corrupt_manifest_fails(cfg: DataConfig, tmp_path: Path) -> None:
    run_ingest(cfg, MINI, root=tmp_path)
    cfg.manifest_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(IngestError, match="unreadable"):
        run_ingest(cfg, MINI, root=tmp_path, tables=["t"])


def test_partial_first_run_records_missing_tables_and_per_table_run_stats(
    cfg: DataConfig, tmp_path: Path
) -> None:
    manifest = run_ingest(cfg, MINI, root=tmp_path, tables=["t"])
    assert manifest["summary"]["tables_missing"] == ["u"]
    assert set(manifest["summary"]["tables"]) == {"t"}
    stats = manifest["summary"]["tables"]["t"]["run"]
    assert stats["peak_rss_bytes"] > 0 and stats["duckdb_version"]
    full = run_ingest(cfg, MINI, root=tmp_path)
    kept = run_ingest(cfg, MINI, root=tmp_path, tables=["u"])
    assert kept["summary"]["tables_missing"] == []
    assert kept["summary"]["tables"]["t"]["run"] == full["summary"]["tables"]["t"]["run"]


def test_config_hash_and_manifest_keys_independent_of_project_root(
    tmp_path: Path, raw_schema: RawSchema
) -> None:
    hashes = []
    for name in ("clone_a", "clone_b"):
        root = tmp_path / name
        (root / "configs").mkdir(parents=True)
        (root / "configs" / "data.yaml").write_text(
            (Path(__file__).parents[2] / "configs" / "data.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        cfg = load_data_config(root / "configs")
        cfg.raw_dir.mkdir(parents=True)
        for csv_name, table in cfg.tables.items():
            header = ",".join(c.name for c in raw_schema.columns(table))
            (cfg.raw_dir / csv_name).write_text(header + "\n", encoding="utf-8")
        manifest = run_ingest(cfg, raw_schema, root=root)
        assert not any(k.startswith("/") for k in {**manifest["inputs"], **manifest["outputs"]})
        assert str(root) not in json.dumps(manifest["summary"]["duckdb"]["configured"])
        hashes.append(manifest["config_hash"])
    assert hashes[0] == hashes[1]


def test_invalid_memory_limit_rejected(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "data.yaml").write_text(
        "raw_dir: r\nparquet_dir: p\nmanifest_path: m.json\ntables: {a.csv: a}\n"
        "duckdb: {memory_limit: lots, temp_directory: t}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="memory_limit"):
        load_data_config(cfg_dir)


def test_duckdb_failure_becomes_ingest_error(cfg: DataConfig, tmp_path: Path) -> None:
    bad = cfg.model_copy(
        update={"duckdb": cfg.duckdb.model_copy(update={"memory_limit": "1B", "threads": 1})}
    )
    with pytest.raises(IngestError):
        run_ingest(bad, MINI, root=tmp_path)
