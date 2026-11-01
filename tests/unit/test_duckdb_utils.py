from pathlib import Path

import pytest

from risklab.config import DuckDBConfig
from risklab.data.duckdb_utils import duckdb_connect

pytestmark = pytest.mark.unit


def test_applies_settings_and_creates_temp_dir(tmp_path: Path) -> None:
    tmp = tmp_path / "spill" / "x"
    cfg = DuckDBConfig(
        memory_limit="512MB", temp_directory=tmp, threads=2, preserve_insertion_order=False
    )
    con = duckdb_connect(cfg)
    try:
        assert tmp.is_dir()
        assert con.execute("SELECT current_setting('threads')").fetchone() == (2,)
        assert con.execute("SELECT current_setting('temp_directory')").fetchone() == (str(tmp),)
        assert con.execute("SELECT current_setting('preserve_insertion_order')").fetchone() == (
            False,
        )
        limit = con.execute("SELECT current_setting('memory_limit')").fetchone()
        assert limit is not None and limit[0].replace(" ", "").lower() in {
            "512.0mb",
            "488.2mib",
            "512mb",
        }
    finally:
        con.close()
