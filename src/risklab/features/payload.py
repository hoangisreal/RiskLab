"""Build public-schema payloads from eligible Train history for parity and load evidence."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from risklab.api.contract import SOURCES, request_model
from risklab.config import load_data_config, load_raw_schema
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.loaders import load_partition
from risklab.features.build import open_batch_connection
from risklab.features.online import GROUP_SOURCE


def public_payload(
    application: dict[str, Any], history: dict[str, list[dict[str, Any]]], *, config_dir: Path
) -> dict[str, Any]:
    """Strip raw keys and replace parent/loan identities with request-local opaque references."""
    contract = request_model(config_dir)
    app_type: Any = contract.model_fields["application"].annotation
    allowed = app_type.model_fields
    application = {name: value for name, value in application.items() if name in allowed}
    bureau = {row["SK_ID_BUREAU"]: f"bureau-{i}" for i, row in enumerate(history.get("bureau", []))}
    previous = {
        row["SK_ID_PREV"]: f"previous-{i}"
        for i, row in enumerate(history.get("previous_applications", []))
    }
    loans = sorted(
        {
            row["SK_ID_PREV"]
            for source in ("installments", "pos_cash", "credit_card")
            for row in history.get(source, [])
        }
    )
    standalone = {loan: f"loan-{i}" for i, loan in enumerate(loans)}
    sources = {}
    for source, rows in history.items():
        records = []
        for row in rows:
            record = {name: value for name, value in row.items() if not name.startswith("SK_ID_")}
            if source in ("bureau", "bureau_balance"):
                record["bureau_ref"] = bureau[row["SK_ID_BUREAU"]]
            elif source == "previous_applications":
                record["prev_ref"] = previous[row["SK_ID_PREV"]]
            else:
                loan = row["SK_ID_PREV"]
                if loan in previous:
                    record["prev_ref"] = previous[loan]
                else:
                    record["loan_ref"] = standalone[loan]
            records.append(record)
        sources[source] = {"status": "provided", "records": records}
    return contract.model_validate({"application": application, "history": sources}).model_dump(
        exclude_none=True
    )


def train_payloads(
    *, applicant_ids: Sequence[int], groups: Sequence[str], config_dir: Path, root: Path
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Extract only Train applicants; historical records use shared temporal/sentinel cleaning."""
    cfg = load_data_config(config_dir)
    raw = load_partition("train", "fit", config_dir=config_dir)
    schema = load_raw_schema(config_dir)
    cleaning = load_cleaning_config(config_dir, schema)
    selected = set(int(value) for value in applicant_ids)
    import pyarrow as pa
    import pyarrow.compute as pc

    raw_selected = raw.filter(pc.is_in(raw["SK_ID_CURR"], value_set=pa.array(list(selected))))
    applications = {row["SK_ID_CURR"]: row for row in raw_selected.to_pylist()}
    if set(applications) != selected:
        raise ValueError("public payload extraction accepts Train applicants only")
    sources = {GROUP_SOURCE[group] for group in groups if group != "application"}
    if "bureau_balance" in sources:
        sources.add("bureau")
    con = open_batch_connection(cfg, applicant_ids, clean_application(raw, cleaning), config_dir)
    try:
        for applicant in applicant_ids:
            history: dict[str, list[dict[str, Any]]] = {}
            for source, table in SOURCES.items():
                if source not in sources:
                    continue
                if source == "bureau_balance":
                    sql = (
                        "SELECT bb.* FROM bureau_balance bb JOIN bureau b USING (SK_ID_BUREAU) "
                        "WHERE b.SK_ID_CURR = ?"
                    )
                else:
                    sql = f"SELECT * FROM {table} WHERE SK_ID_CURR = ?"
                history[source] = con.sql(sql, params=[int(applicant)]).to_arrow_table().to_pylist()
            yield (
                int(applicant),
                public_payload(applications[int(applicant)], history, config_dir=config_dir),
            )
    finally:
        con.close()
