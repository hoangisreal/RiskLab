"""Reproduction redirects artifacts without changing the source partition protocol or data."""

import pytest
import yaml
from scripts.reproduce import prepare_output

from risklab.config import load_data_config, load_partition_config
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.unit


def test_isolated_output_preserves_raw_source_and_partition_seed(tmp_path):
    out = tmp_path / "fresh"
    config = prepare_output(CONFIG_DIR, out)
    source_data = load_data_config(CONFIG_DIR)
    fresh_data = load_data_config(config)
    source_partition = load_partition_config(CONFIG_DIR)
    fresh_partition = load_partition_config(config)
    assert fresh_data.raw_dir == source_data.raw_dir
    assert fresh_data.parquet_dir == out / "data/parquet"
    assert fresh_data.reports_dir == out / "reports"
    assert fresh_partition.seed == source_partition.seed
    assert fresh_partition.shares == source_partition.shares
    assert fresh_partition.output_dir != source_partition.output_dir
    assert (out / "sql/features/installments.sql").read_bytes() == (
        CONFIG_DIR.parent / "sql/features/installments.sql"
    ).read_bytes()
    assert yaml.safe_load((config / "cost.yaml").read_text()) == yaml.safe_load(
        (CONFIG_DIR / "cost.yaml").read_text()
    )
    with pytest.raises(ValueError, match="absent or empty"):
        prepare_output(CONFIG_DIR, out)
