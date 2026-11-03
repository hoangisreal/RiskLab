"""Ambiguous parent identities and invalid payment records fail before feature aggregation."""

import copy

import pytest

from risklab.api.service import ServiceError
from risklab.api.validation import validate_history_records

pytestmark = pytest.mark.unit


def test_parent_and_month_duplicates_are_rejected():
    history = {
        "bureau": {"status": "provided", "records": [{"bureau_ref": "b"}, {"bureau_ref": "b"}]}
    }
    with pytest.raises(ServiceError, match="duplicate bureau_ref"):
        validate_history_records(history, ["bureau"])
    # Structurally valid but unused sources remain ignored.
    validate_history_records(history, [])
    history = {
        "pos_cash": {
            "status": "provided",
            "records": [
                {"loan_ref": "l", "MONTHS_BALANCE": -1},
                {"loan_ref": "l", "MONTHS_BALANCE": -1},
            ],
        }
    }
    with pytest.raises(ServiceError, match="duplicate loan-month"):
        validate_history_records(history, ["pos_cash"])


def test_partial_payments_allowed_but_conflicting_or_exact_duplicates_are_not():
    row = {
        "loan_ref": "l",
        "NUM_INSTALMENT_VERSION": 1,
        "NUM_INSTALMENT_NUMBER": 1,
        "DAYS_INSTALMENT": -5,
        "DAYS_ENTRY_PAYMENT": -6,
        "AMT_PAYMENT": 40,
    }
    history = {"installments": {"status": "provided", "records": [row, {**row, "AMT_PAYMENT": 60}]}}
    validate_history_records(history, ["installments"])
    duplicate = copy.deepcopy(history)
    duplicate["installments"]["records"][1] = row
    with pytest.raises(ServiceError, match="exact duplicate"):
        validate_history_records(duplicate, ["installments"])
    conflict = copy.deepcopy(history)
    conflict["installments"]["records"][1]["DAYS_INSTALMENT"] = -4
    with pytest.raises(ServiceError, match="conflicting due dates"):
        validate_history_records(conflict, ["installments"])
    history["previous_applications"] = {"status": "provided", "records": [{"prev_ref": "l"}]}
    with pytest.raises(ServiceError, match="standalone loan_ref"):
        validate_history_records(history, ["installments", "previous"])
