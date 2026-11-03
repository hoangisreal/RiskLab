"""Applicant-local relational request constraints beyond JSON field types."""

from __future__ import annotations

import json
from typing import Any

from risklab.api.service import ServiceError
from risklab.features.online import GROUP_SOURCE


def validate_history_records(history: dict[str, Any], groups: list[str]) -> None:
    """Reject ambiguous references and duplicate/conflicting records in consumed sources."""
    needed = {GROUP_SOURCE[group] for group in groups if group != "application"}
    if "bureau_balance" in needed:
        needed.add("bureau")
    if any(
        row.get("prev_ref") is not None
        for source in ("installments", "pos_cash", "credit_card")
        if source in needed
        for row in history.get(source, {}).get("records", [])
    ):
        needed.add("previous_applications")
    for source, key in (("bureau", "bureau_ref"), ("previous_applications", "prev_ref")):
        if source in needed:
            refs = [row[key] for row in history.get(source, {}).get("records", [])]
            if len(refs) != len(set(refs)):
                raise ServiceError(f"{source} contains duplicate {key}")
    parent_refs = {
        row["prev_ref"] for row in history.get("previous_applications", {}).get("records", [])
    }
    for source in needed:
        rows = history.get(source, {}).get("records", [])
        if source in ("installments", "pos_cash", "credit_card") and any(
            row.get("loan_ref") in parent_refs for row in rows if row.get("loan_ref") is not None
        ):
            raise ServiceError("standalone loan_ref must be distinct from parent prev_ref")
        if source in ("bureau_balance", "pos_cash", "credit_card"):

            def month_key(row: dict[str, Any]) -> tuple[Any, ...]:
                return (
                    row.get("bureau_ref"),
                    row.get("prev_ref"),
                    row.get("loan_ref"),
                    row["MONTHS_BALANCE"],
                )

            keys = [month_key(row) for row in rows]
            if len(keys) != len(set(keys)):
                raise ServiceError(f"{source} contains duplicate loan-month records")
        if source == "installments":
            records = [json.dumps(row, sort_keys=True) for row in rows]
            if len(records) != len(set(records)):
                raise ServiceError("installments contains exact duplicate payment records")
            due_dates: dict[tuple[Any, ...], set[Any]] = {}
            for row in rows:
                if (
                    row.get("NUM_INSTALMENT_VERSION") is None
                    or row.get("NUM_INSTALMENT_NUMBER") is None
                ):
                    raise ServiceError("installments requires version and installment number")
                installment_key = (
                    row.get("prev_ref"),
                    row.get("loan_ref"),
                    row["NUM_INSTALMENT_VERSION"],
                    row["NUM_INSTALMENT_NUMBER"],
                )
                due_dates.setdefault(installment_key, set()).add(row["DAYS_INSTALMENT"])
            if any(len(dates) > 1 for dates in due_dates.values()):
                raise ServiceError(
                    "installments contains conflicting due dates for one installment"
                )
