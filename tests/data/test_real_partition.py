import json

import pytest

from risklab.config import PARTITION_ROLES, load_data_config, load_partition_config
from risklab.data.partition import class_counts, run_partition
from tests.conftest import CONFIG_DIR, REPO_ROOT

pytestmark = pytest.mark.data

N_ROWS = 307_511
PREVALENCE = 0.08073


@pytest.mark.slow
def test_real_partitions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    data_cfg = load_data_config(CONFIG_DIR)
    if (
        not data_cfg.manifest_path.is_file()
        or not (data_cfg.parquet_dir / "application_train.parquet").is_file()
    ):
        pytest.skip("ingested real data is absent")
    cfg = load_partition_config(CONFIG_DIR).model_copy(update={"output_dir": tmp_path / "p"})
    data_cfg = data_cfg.model_copy(update={"reports_dir": tmp_path / "reports"})
    result = run_partition(cfg, data_cfg, root=REPO_ROOT)
    doc = result["document"]
    assert doc["rows"] == N_ROWS == sum(doc["roles"][r]["rows"] for r in PARTITION_ROLES)
    n_pos = doc["positives"]
    for role in PARTITION_ROLES:
        stats = doc["roles"][role]
        exp_pos = class_counts(n_pos, cfg.shares)[role]
        exp_neg = class_counts(N_ROWS - n_pos, cfg.shares)[role]
        assert stats["positives"] == exp_pos
        assert stats["rows"] == exp_pos + exp_neg
        assert abs(stats["rows"] - cfg.shares[role] * N_ROWS) <= 2
        assert abs(stats["positive_rate"] - PREVALENCE) < 0.0005
    assert json.loads((cfg.output_dir / "partitions.json").read_text()) == doc
