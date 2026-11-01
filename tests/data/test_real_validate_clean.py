import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from risklab.config import load_data_config, load_model, load_raw_schema
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.validate import ApplicationDomains, ValidationRules, run_validate
from tests.conftest import CONFIG_DIR, REPO_ROOT

pytestmark = pytest.mark.data

TRAIN_ROWS = 307_511
SENTINEL_TRAIN, SENTINEL_TEST = 55_374, 9_274
FLAG = "DAYS_EMPLOYED_IS_SENTINEL"
# Known finding (docs/data_contract.md): one test row has REGION_RATING_CLIENT_W_CITY = -1. It is
# accepted by a known exception in application_validation.yaml and reported separately.
ACCEPTED_ID = 224393


@pytest.fixture(scope="module")
def data_cfg():  # type: ignore[no-untyped-def]
    cfg = load_data_config(CONFIG_DIR)
    if (
        not cfg.manifest_path.is_file()
        or not (cfg.parquet_dir / "application_train.parquet").is_file()
    ):
        pytest.skip("ingested real data is absent")
    return cfg


def test_real_validation(data_cfg, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cfg = data_cfg.model_copy(update={"reports_dir": tmp_path / "reports"})
    schema = load_raw_schema(CONFIG_DIR, data_cfg=cfg)
    rules = load_model(CONFIG_DIR, "application_validation.yaml", ValidationRules)
    domains = load_model(CONFIG_DIR, "application_domains.yaml", ApplicationDomains)
    doc = run_validate(cfg, schema, rules, domains, root=REPO_ROOT)
    errors = {
        (c["name"], c["table"]): c["offending_rows"]
        for c in doc["checks"]
        if c["severity"] == "error" and c["offending_rows"] > 0
    }
    assert not errors
    accepted = [
        (c["name"], c["table"], c["accepted_exceptions"], c["status"])
        for c in doc["checks"]
        if "accepted_exceptions" in c
    ]
    assert len(accepted) == 1
    name, table, exc, status = accepted[0]
    assert (name, table, status) == (
        "range:region_rating",
        "application_test",
        "passed_with_exceptions",
    )
    assert (exc["ids"], exc["count"]) == ([ACCEPTED_ID], 1)
    assert not [c for c in doc["checks"] if c["name"] == "stale_known_exception"]
    saved = json.loads((tmp_path / "reports" / "validation" / "application.json").read_text())
    assert saved["status"] == "passed"
    assert saved["summary"]["errors"] == 0
    assert saved["summary"]["accepted_exceptions"] == 1


@pytest.mark.parametrize(
    ("table", "rows", "sentinels"),
    [
        ("application_train", TRAIN_ROWS, SENTINEL_TRAIN),
        ("application_test", 48_744, SENTINEL_TEST),
    ],
)
def test_real_cleaning(data_cfg, table: str, rows: int, sentinels: int) -> None:  # type: ignore[no-untyped-def]
    schema = load_raw_schema(CONFIG_DIR, data_cfg=data_cfg)
    cfg = load_cleaning_config(CONFIG_DIR, schema)
    raw = pq.read_table(data_cfg.parquet_dir / f"{table}.parquet")
    clean = clean_application(raw, cfg)
    assert clean.num_rows == raw.num_rows == rows
    assert clean["SK_ID_CURR"].equals(raw["SK_ID_CURR"])
    if "TARGET" in raw.column_names:
        assert clean["TARGET"].equals(raw["TARGET"])
    assert sum(clean[FLAG].to_pylist()) == sentinels
    assert clean["DAYS_EMPLOYED"].null_count - raw["DAYS_EMPLOYED"].null_count == sentinels

    def xna(col: str) -> int:
        return sum(1 for v in raw[col].to_pylist() if v == "XNA")

    for col in ("CODE_GENDER", "ORGANIZATION_TYPE"):
        assert clean[col].null_count - raw[col].null_count == xna(col)
    assert xna("ORGANIZATION_TYPE") == sentinels
    if table == "application_train":
        assert xna("CODE_GENDER") == 4
