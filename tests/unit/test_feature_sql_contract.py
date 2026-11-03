"""Hand-calculated applicant-local aggregates, partial payments and temporal isolation."""

import duckdb
import pyarrow as pa
import pytest

from risklab.config import load_raw_schema
from risklab.features.build import build_group_frames
from risklab.features.history_clean import install_history_views
from risklab.features.online import _records_to_table
from risklab.features.registry import load_registry
from tests.conftest import CONFIG_DIR, REPO_ROOT

pytestmark = pytest.mark.unit


@pytest.fixture
def histories():
    return {
        "bureau": [
            dict(
                SK_ID_CURR=1,
                SK_ID_BUREAU=10,
                DAYS_CREDIT=-50,
                CREDIT_ACTIVE="Active",
                AMT_CREDIT_SUM_DEBT=100,
                AMT_CREDIT_SUM_LIMIT=200,
                AMT_CREDIT_SUM_OVERDUE=0,
                CREDIT_DAY_OVERDUE=0,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_BUREAU=11,
                DAYS_CREDIT=-100,
                CREDIT_ACTIVE="Closed",
                AMT_CREDIT_SUM_DEBT=0,
                AMT_CREDIT_SUM_LIMIT=100,
                AMT_CREDIT_SUM_OVERDUE=0,
                CREDIT_DAY_OVERDUE=0,
            ),
            dict(SK_ID_CURR=1, SK_ID_BUREAU=12, DAYS_CREDIT=1, CREDIT_ACTIVE="Active"),
        ],
        "bureau_balance": [
            dict(SK_ID_BUREAU=10, MONTHS_BALANCE=-3, STATUS="2"),
            dict(SK_ID_BUREAU=10, MONTHS_BALANCE=-1, STATUS="0"),
            dict(SK_ID_BUREAU=11, MONTHS_BALANCE=0, STATUS="0"),
            dict(SK_ID_BUREAU=10, MONTHS_BALANCE=1, STATUS="5"),
            dict(SK_ID_BUREAU=999, MONTHS_BALANCE=-1, STATUS="5"),
        ],
        "previous_application": [
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                DAYS_DECISION=-20,
                NAME_CONTRACT_STATUS="Refused",
                NAME_CONTRACT_TYPE="Cash loans",
                AMT_CREDIT=80,
                AMT_APPLICATION=100,
                DAYS_FIRST_DUE=365243,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=102,
                DAYS_DECISION=-1,
                NAME_CONTRACT_STATUS="Approved",
                NAME_CONTRACT_TYPE="XNA",
                AMT_CREDIT=40,
                AMT_APPLICATION=50,
            ),
            dict(SK_ID_CURR=1, SK_ID_PREV=101, DAYS_DECISION=1),
        ],
        "installments_payments": [
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                NUM_INSTALMENT_VERSION=1,
                NUM_INSTALMENT_NUMBER=1,
                DAYS_INSTALMENT=-10,
                DAYS_ENTRY_PAYMENT=-12,
                AMT_INSTALMENT=100,
                AMT_PAYMENT=40,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                NUM_INSTALMENT_VERSION=1,
                NUM_INSTALMENT_NUMBER=1,
                DAYS_INSTALMENT=-10,
                DAYS_ENTRY_PAYMENT=-8,
                AMT_INSTALMENT=100,
                AMT_PAYMENT=60,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                NUM_INSTALMENT_VERSION=1,
                NUM_INSTALMENT_NUMBER=2,
                DAYS_INSTALMENT=-5,
                DAYS_ENTRY_PAYMENT=-7,
                AMT_INSTALMENT=100,
                AMT_PAYMENT=40,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=200,
                NUM_INSTALMENT_VERSION=1,
                NUM_INSTALMENT_NUMBER=1,
                DAYS_INSTALMENT=-20,
                DAYS_ENTRY_PAYMENT=-10,
                AMT_INSTALMENT=100,
                AMT_PAYMENT=80,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=200,
                NUM_INSTALMENT_VERSION=1,
                NUM_INSTALMENT_NUMBER=2,
                DAYS_INSTALMENT=-1,
                DAYS_ENTRY_PAYMENT=1,
                AMT_INSTALMENT=100,
                AMT_PAYMENT=100,
            ),
        ],
        "pos_cash_balance": [
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                MONTHS_BALANCE=-2,
                SK_DPD=0,
                SK_DPD_DEF=0,
                NAME_CONTRACT_STATUS="Completed",
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                MONTHS_BALANCE=-1,
                SK_DPD=10,
                SK_DPD_DEF=1,
                NAME_CONTRACT_STATUS="Active",
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=200,
                MONTHS_BALANCE=-1,
                SK_DPD=20,
                SK_DPD_DEF=1,
                NAME_CONTRACT_STATUS="Active",
            ),
        ],
        "credit_card_balance": [
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                MONTHS_BALANCE=-2,
                AMT_BALANCE=10,
                AMT_CREDIT_LIMIT_ACTUAL=100,
                SK_DPD=0,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=100,
                MONTHS_BALANCE=-1,
                AMT_BALANCE=30,
                AMT_CREDIT_LIMIT_ACTUAL=100,
                SK_DPD=10,
            ),
            dict(
                SK_ID_CURR=1,
                SK_ID_PREV=200,
                MONTHS_BALANCE=-1,
                AMT_BALANCE=80,
                AMT_CREDIT_LIMIT_ACTUAL=100,
                SK_DPD=20,
            ),
        ],
    }


def build(histories, threads=2):
    con = duckdb.connect(config={"threads": threads})
    schema = load_raw_schema(CONFIG_DIR)
    for table, records in histories.items():
        con.register("raw_" + table, _records_to_table(records, schema.columns(table)))
    con.register("applicants", pa.table({"SK_ID_CURR": [1, 2]}))
    con.register(
        "application_src",
        pa.table(
            {
                "SK_ID_CURR": [1, 2],
                "AMT_CREDIT": [100.0, 0.0],
                "AMT_INCOME_TOTAL": [50.0, 0.0],
                "AMT_ANNUITY": [10.0, 0.0],
                "AMT_GOODS_PRICE": [50.0, 0.0],
                "DAYS_EMPLOYED": [-50.0, None],
                "DAYS_BIRTH": [-500.0, -1000.0],
                "CNT_FAM_MEMBERS": [2.0, 0.0],
            }
        ),
    )
    install_history_views(con, config_dir=CONFIG_DIR, schema=schema)
    assert (
        con.sql("SELECT DAYS_FIRST_DUE FROM previous_application WHERE SK_ID_PREV=100").fetchall()[
            0
        ][0]
        is None
    )
    frames = build_group_frames(
        con, load_registry(CONFIG_DIR).all_groups(), load_registry(CONFIG_DIR), REPO_ROOT
    )
    con.close()
    return frames


def test_every_sql_known_values_and_no_history(histories):
    frames = build(histories)
    expected = {
        "application": {
            "app_credit_income_ratio": 2,
            "app_annuity_income_ratio": 0.2,
            "app_credit_goods_ratio": 2,
            "app_employment_age_ratio": 0.1,
            "app_income_per_member": 25,
        },
        "bureau": {
            "bureau_record_count": 2,
            "bureau_active_ratio": 0.5,
            "bureau_debt_sum": 100,
            "bureau_limit_sum": 300,
            "bureau_overdue_sum": 0,
            "bureau_debt_limit_ratio": 1 / 3,
            "bureau_max_overdue_days": 0,
            "bureau_mean_days_credit": 75,
        },
        "bureau_balance": {
            "bb_record_count": 3,
            "bb_dpd_months": 1,
            "bb_dpd_ratio": 0.25,
            "bb_max_status": 2,
            "bb_months_span": 1,
        },
        "previous": {
            "prev_record_count": 2,
            "prev_refusal_rate": 0.5,
            "prev_approved_amount_ratio": 0.8,
            "prev_mean_credit": 60,
            "prev_mean_days_since_decision": 10.5,
            "prev_cash_share": 1,
            "prev_approval_rate": 0.5,
        },
        "installments": {
            "inst_record_count": 3,
            "inst_late_ratio": 0.75,
            "inst_mean_days_late": 5.5,
            "inst_max_days_late": 10,
            "inst_underpaid_ratio": 0.75,
            "inst_payment_ratio_mean": 0.75,
            "inst_recent_late_ratio": 0.75,
        },
        "pos_cash": {
            "pos_record_count": 3,
            "pos_mean_dpd": 12.5,
            "pos_max_dpd": 20,
            "pos_dpd_def_ratio": 0.75,
            "pos_active_share": 0.75,
        },
        "credit_card": {
            "cc_record_count": 3,
            "cc_mean_utilization": 0.5,
            "cc_max_utilization": 0.8,
            "cc_mean_dpd": 12.5,
            "cc_max_dpd": 20,
            "cc_mean_balance": 50,
        },
    }
    for group, values in expected.items():
        assert set(frames[group].columns) == set(values)
        for col, value in values.items():
            assert frames[group].loc[1, col] == pytest.approx(value), col
        if group != "application":
            counts = [c for c in frames[group] if c.endswith("_record_count")]
            assert frames[group].loc[2, counts[0]] == 0
            assert frames[group].loc[2].drop(counts).isna().all()


def test_other_applicant_and_future_rows_cannot_change_features(histories):
    baseline = build(histories)
    histories["installments_payments"].append(
        dict(
            SK_ID_CURR=2,
            SK_ID_PREV=900,
            NUM_INSTALMENT_VERSION=1,
            NUM_INSTALMENT_NUMBER=1,
            DAYS_INSTALMENT=-1,
            DAYS_ENTRY_PAYMENT=-1,
            AMT_INSTALMENT=1000000,
            AMT_PAYMENT=1,
        )
    )
    histories["installments_payments"].append(
        dict(
            SK_ID_CURR=1,
            SK_ID_PREV=100,
            NUM_INSTALMENT_VERSION=2,
            NUM_INSTALMENT_NUMBER=99,
            DAYS_INSTALMENT=1,
            DAYS_ENTRY_PAYMENT=-1,
            AMT_INSTALMENT=1000000,
            AMT_PAYMENT=1,
        )
    )
    changed = build(histories)
    for group in baseline:
        assert baseline[group].loc[1].equals(changed[group].loc[1])


def test_floating_aggregates_are_exactly_stable_across_row_orders_and_threads(histories):
    import random

    histories["bureau"].extend(
        [
            dict(
                SK_ID_CURR=1,
                SK_ID_BUREAU=20 + i,
                DAYS_CREDIT=-1,
                AMT_CREDIT_SUM_DEBT=value,
                AMT_CREDIT_SUM_LIMIT=1,
            )
            for i, value in enumerate([1e16, 1.0, 1.0, 0.01, 0.02, 0.03])
        ]
    )
    baseline = build(histories, threads=1)
    rng = random.Random(4)
    for _ in range(4):
        for rows in histories.values():
            rng.shuffle(rows)
        result = build(histories, threads=4)
        for group, expected in baseline.items():
            assert expected.equals(result[group]), group
