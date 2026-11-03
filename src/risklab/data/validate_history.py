"""Historical-table validation: FK coverage, temporal validity, duplicate keys.

Counts only; no applicant-level statistic is computed from TARGET. Orphan monthly rows
keyed on SK_ID_CURR are still usable by the feature SQL, while orphan bureau_balance rows
(no SK_ID_CURR) can never be attributed and are only counted here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from risklab.config import DataConfig
from risklab.data.duckdb_utils import duckdb_connect
from risklab.data.ingest import quote_str


class HistoryValidationError(Exception):
    """Raised when the Parquet inputs are missing."""


_QUERIES: dict[str, str] = {
    "bureau_balance_orphan_rows": (
        "SELECT COUNT(*) FROM bureau_balance bb ANTI JOIN bureau b USING (SK_ID_BUREAU)"
    ),
    "pos_orphan_rows": (
        "SELECT COUNT(*) FROM pos_cash_balance p "
        "ANTI JOIN previous_application pa USING (SK_ID_PREV)"
    ),
    "cc_orphan_rows": (
        "SELECT COUNT(*) FROM credit_card_balance c "
        "ANTI JOIN previous_application pa USING (SK_ID_PREV)"
    ),
    "inst_orphan_rows": (
        "SELECT COUNT(*) FROM installments_payments i "
        "ANTI JOIN previous_application pa USING (SK_ID_PREV)"
    ),
    "bureau_enddate_fact_positive": "SELECT COUNT(*) FROM bureau WHERE DAYS_ENDDATE_FACT > 0",
    "inst_entry_payment_positive": (
        "SELECT COUNT(*) FROM installments_payments WHERE DAYS_ENTRY_PAYMENT > 0"
    ),
    "bureau_balance_months_positive": (
        "SELECT COUNT(*) FROM bureau_balance WHERE MONTHS_BALANCE > 0"
    ),
    "pos_months_positive": "SELECT COUNT(*) FROM pos_cash_balance WHERE MONTHS_BALANCE > 0",
    "cc_months_positive": "SELECT COUNT(*) FROM credit_card_balance WHERE MONTHS_BALANCE > 0",
    "bureau_balance_dup_keys": (
        "SELECT COUNT(*) - COUNT(*) FILTER (WHERE TRUE) * 0 - "
        "(SELECT COUNT(*) FROM (SELECT DISTINCT SK_ID_BUREAU, MONTHS_BALANCE FROM bureau_balance)) "
        "FROM bureau_balance"
    ),
}


def validate_history(data_cfg: DataConfig) -> dict[str, Any]:
    """Run the count checks over the ingested Parquet files."""
    parquet = data_cfg.parquet_dir
    tables = (
        "bureau",
        "bureau_balance",
        "previous_application",
        "pos_cash_balance",
        "credit_card_balance",
        "installments_payments",
    )
    for table in tables:
        if not (parquet / f"{table}.parquet").is_file():
            raise HistoryValidationError(
                f"Parquet file not found: {parquet / (table + '.parquet')}"
            )
    con = duckdb_connect(data_cfg.duckdb)
    try:
        for table in tables:
            con.execute(
                "CREATE OR REPLACE VIEW "
                + table
                + " AS SELECT * FROM read_parquet("
                + quote_str(str(parquet / f"{table}.parquet"))
                + ")"
            )
        counts: dict[str, int] = {}
        for name, sql in _QUERIES.items():
            if name == "bureau_balance_dup_keys":
                total = con.execute("SELECT COUNT(*) FROM bureau_balance").fetchone()
                distinct = con.execute(
                    "SELECT COUNT(*) FROM (SELECT DISTINCT SK_ID_BUREAU, "
                    "MONTHS_BALANCE FROM bureau_balance)"
                ).fetchone()
                counts[name] = int((total[0] if total else 0) - (distinct[0] if distinct else 0))
                continue
            row = con.execute(sql).fetchone()
            counts[name] = int(row[0]) if row else 0
    except duckdb.Error as exc:
        raise HistoryValidationError(f"DuckDB error during history validation: {exc}") from exc
    finally:
        con.close()
    temporal_ok = all(
        counts[k] == 0
        for k in (
            "bureau_enddate_fact_positive",
            "inst_entry_payment_positive",
            "bureau_balance_months_positive",
            "pos_months_positive",
            "cc_months_positive",
        )
    )
    return {"counts": counts, "temporal_valid": temporal_ok}


def sql_files_reference_target(sql_dir: Path) -> list[str]:
    """Files under ``sql_dir`` mentioning TARGET (must be empty for feature SQL)."""
    hits: list[str] = []
    for path in sorted(sql_dir.rglob("*.sql")):
        if "TARGET" in path.read_text(encoding="utf-8"):
            hits.append(str(path))
    return hits


__all__ = ["HistoryValidationError", "sql_files_reference_target", "validate_history"]
