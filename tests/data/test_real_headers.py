import csv
import json

import pytest

from risklab.config import DataConfig, RawSchema

pytestmark = pytest.mark.data


def test_real_csv_headers_match_raw_schema(
    repo_data_config: DataConfig, raw_schema: RawSchema
) -> None:
    if not repo_data_config.raw_dir.is_dir():
        pytest.skip("data/raw is absent")
    for csv_name, table in repo_data_config.tables.items():
        path = repo_data_config.raw_dir / csv_name
        if not path.is_file():
            pytest.skip(f"{path} is absent")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
        assert header == [c.name for c in raw_schema.columns(table)], table


@pytest.mark.slow
def test_manifest_csv_rows_match_independent_count(repo_data_config: DataConfig) -> None:
    manifest_path = repo_data_config.manifest_path
    if not manifest_path.is_file() or not repo_data_config.raw_dir.is_dir():
        pytest.skip("data/manifest.json or data/raw is absent")
    tables = json.loads(manifest_path.read_text())["summary"]["tables"]
    for csv_name, table in repo_data_config.tables.items():
        path = repo_data_config.raw_dir / csv_name
        if not path.is_file() or table not in tables:
            pytest.skip(f"{table} is not ingested or {path} is absent")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            next(reader)
            count = sum(1 for _ in reader)
        assert tables[table]["csv_rows"] == count, table
