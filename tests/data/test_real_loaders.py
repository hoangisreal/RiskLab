import json

import pytest

from risklab.config import load_data_config, load_partition_config
from risklab.data.loaders import (
    ALLOWED_PURPOSE,
    PartitionRole,
    TestAccessError,
    load_partition,
    partition_info,
)
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.data


def test_real_role_counts_match_partitions_json() -> None:
    data_cfg = load_data_config(CONFIG_DIR)
    cfg = load_partition_config(CONFIG_DIR)
    doc_path = cfg.output_dir / "partitions.json"
    if not doc_path.is_file() or not (data_cfg.parquet_dir / "application_train.parquet").is_file():
        pytest.skip("ingested and partitioned real data is absent")
    doc = json.loads(doc_path.read_text())
    for role, purpose in ALLOWED_PURPOSE.items():
        table = load_partition(role, purpose, columns=["AMT_CREDIT"], config_dir=CONFIG_DIR)
        stats = doc["roles"][role.value]
        assert table.num_rows == stats["rows"]
        assert sum(table["TARGET"].to_pylist()) == stats["positives"]
        info = partition_info(role, config_dir=CONFIG_DIR)
        assert info.partition_hash == doc["partition_hash"]
    with pytest.raises(TestAccessError):
        load_partition(PartitionRole.test, "select", config_dir=CONFIG_DIR)
