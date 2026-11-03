"""Historical exclusions, key conflicts and source guards use synthetic history only."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from risklab.data.validate_history import (
    HistoryValidationError,
    ensure_history_validated,
    validate_history,
)

pytestmark = pytest.mark.integration


def test_temporal_reasons_and_duplicate_conflicts(app_env):
    cfg = app_env.data_cfg
    before = validate_history(cfg, config_dir=app_env.config_dir)
    path = cfg.parquet_dir / "installments_payments.parquet"
    original = path.read_bytes()
    try:
        table = pq.read_table(path)
        row = table.to_pylist()[0]
        future = {**row, "NUM_INSTALMENT_NUMBER": 10001, "DAYS_ENTRY_PAYMENT": 1}
        missing = {**row, "NUM_INSTALMENT_NUMBER": 10002, "DAYS_ENTRY_PAYMENT": None}
        pq.write_table(
            pa.concat_tables([table, pa.Table.from_pylist([future, missing], schema=table.schema)]),
            path,
        )
        report = validate_history(cfg, config_dir=app_env.config_dir)
        assert report["passed"]
        assert (
            report["excluded_rows"]["installments_payments"]
            == before["excluded_rows"]["installments_payments"] + 2
        )
        for reason in ("future", "missing"):
            key = f"installments_payments.DAYS_ENTRY_PAYMENT.{reason}"
            assert report["exclusion_reasons"][key] == before["exclusion_reasons"][key] + 1
        pq.write_table(pa.concat_tables([table, table.slice(0, 1)]), path)
        report = validate_history(cfg, config_dir=app_env.config_dir)
        assert not report["passed"]
        assert "installment_exact_duplicates" in report["errors"]
    finally:
        path.write_bytes(original)


def test_cached_validation_cannot_hide_source_mutation(app_env):
    cfg = app_env.data_cfg
    ensure_history_validated(cfg, app_env.config_dir)
    path = cfg.raw_dir / "bureau.csv"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        with pytest.raises(HistoryValidationError, match="no longer matches ingest"):
            ensure_history_validated(cfg, app_env.config_dir)
    finally:
        path.write_bytes(original)
