"""Synthetic Home Credit style dataset for tests and CI (numpy only, deterministic per seed).

Column names, order and types come from ``raw_schema.yaml``; columns without a dedicated rule get
a generic value by type and name prefix. Key columns, time fields, sentinels and ``TARGET`` have
explicit rules. Nothing here is derived from the real data.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from risklab.config import ColumnSpec, RawSchema

Arr = NDArray[Any]
SENTINEL_DAYS = 365243
TRAIN_SHARE = 0.85
TARGET_PREVALENCE = 0.08
MIN_APPLICANTS = 50
INT_TYPES = {"BIGINT", "INTEGER", "SMALLINT", "TINYINT"}
CORE_TABLES = (
    "application_train",
    "application_test",
    "bureau",
    "bureau_balance",
    "previous_application",
    "pos_cash_balance",
    "credit_card_balance",
    "installments_payments",
)

Rule = Callable[[], Arr]


def _generic(rng: np.random.Generator, name: str, typ: str, n: int) -> Arr:
    if typ == "VARCHAR":
        return rng.choice(np.array(["A", "B", "C"]), n)
    if name.startswith(("FLAG_", "NFLAG_")):
        return rng.integers(0, 2, n)
    if name.startswith("DAYS_"):
        return -rng.integers(1, 3000, n)
    if name.startswith(("CNT_", "SK_DPD", "AMT_REQ", "OBS_", "DEF_")):
        return rng.poisson(1.5, n)
    if name.startswith("AMT_"):
        return rng.lognormal(10.0, 1.0, n)
    if name.endswith(("_AVG", "_MODE", "_MEDI")):
        return rng.random(n)
    if typ in INT_TYPES:
        return rng.integers(0, 10, n)
    return rng.normal(0.0, 1.0, n)


def _format(values: Arr, typ: str) -> Arr:
    if typ == "VARCHAR":
        return values.astype(str)
    if typ in INT_TYPES:
        return np.rint(values).astype(np.int64).astype(str)
    return np.round(values.astype(np.float64), 4).astype(str)


def _build_table(
    rng: np.random.Generator,
    table: str,
    cols: list[ColumnSpec],
    n: int,
    rules: Mapping[str, Rule],
    null_rates: Mapping[str, float],
    *,
    never_null: tuple[str, ...] = ("SK_ID_CURR", "SK_ID_PREV", "SK_ID_BUREAU", "TARGET"),
) -> dict[str, Arr]:
    """Format every schema column as strings; empty string is NULL (as in the raw CSVs)."""
    out: dict[str, Arr] = {}
    for col in cols:
        rule = rules.get(col.name)
        values = rule() if rule is not None else _generic(rng, col.name, col.type, n)
        if len(values) != n:
            raise AssertionError(f"{table}.{col.name}: rule returned {len(values)} rows, need {n}")
        strs = _format(np.asarray(values), col.type)
        rate = null_rates.get(col.name, 0.0)
        if rate == 0.0 and col.name.endswith(("_AVG", "_MODE", "_MEDI")):
            rate = 0.5
        if rate > 0.0 and col.name not in never_null:
            strs[rng.random(n) < rate] = ""
        out[col.name] = strs
    return out


def _write_csv(path: Path, header: list[str], columns: list[Arr]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(zip(*(c.tolist() for c in columns), strict=True))


def _zscore(x: Arr) -> Arr:
    sd = float(x.std())
    return np.asarray((x - x.mean()) / (sd if sd > 0 else 1.0))


def _intercept_for_prevalence(score: Arr, prevalence: float) -> float:
    lo, hi = -20.0, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if float((1 / (1 + np.exp(-(mid + score)))).mean()) < prevalence:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _group_positions(counts: Arr) -> Arr:
    """1-based position of each row inside its group, for groups laid out contiguously."""
    starts = np.cumsum(counts) - counts
    return np.asarray(np.arange(int(counts.sum())) - np.repeat(starts, counts) + 1)


def generate(
    out_dir: Path,
    schema: RawSchema,
    csv_names: Mapping[str, str],
    *,
    n_applicants: int,
    seed: int,
) -> dict[str, int]:
    """Write the eight raw tables as CSVs into ``out_dir``; returns data rows per table."""
    if n_applicants < MIN_APPLICANTS:
        raise ValueError(f"n_applicants must be >= {MIN_APPLICANTS}")
    missing = [t for t in CORE_TABLES if t not in csv_names or t not in schema.tables]
    if missing:
        raise ValueError(f"Tables missing from csv_names or raw_schema: {missing}")
    rng = np.random.default_rng(seed)
    n = n_applicants
    n_train = int(round(n * TRAIN_SHARE))
    tables: dict[str, dict[str, Arr]] = {}

    # ---- applicants (train + test share one generated frame) -------------------------------
    app_ids = 100_001 + rng.permutation(n)
    quality = rng.random(n)
    ext = [np.clip(quality + rng.normal(0, 0.2, n), 0.01, 0.99) for _ in range(3)]
    income = rng.lognormal(12.0, 0.5, n)
    credit = income * rng.lognormal(1.0, 0.5, n)
    employed = -rng.integers(30, 15000, n)
    pensioner = rng.random(n) < 0.18
    employed = np.where(pensioner, SENTINEL_DAYS, employed)
    gender = rng.choice(np.array(["F", "M", "XNA"]), n, p=[0.64, 0.355, 0.005])
    gender[0] = "XNA"
    org = rng.choice(
        np.array(["Business Entity", "Self-employed", "Government", "School", "Trade"]), n
    )
    org = np.where(pensioner, "XNA", org)
    cat = rng.choice
    app_rules: dict[str, Rule] = {
        "SK_ID_CURR": lambda: app_ids,
        "NAME_CONTRACT_TYPE": lambda: cat(np.array(["Cash loans", "Revolving loans"]), n),
        "CODE_GENDER": lambda: gender,
        "FLAG_OWN_CAR": lambda: cat(np.array(["Y", "N"]), n),
        "FLAG_OWN_REALTY": lambda: cat(np.array(["Y", "N"]), n),
        "CNT_CHILDREN": lambda: rng.poisson(0.4, n),
        "AMT_INCOME_TOTAL": lambda: income,
        "AMT_CREDIT": lambda: credit,
        "AMT_ANNUITY": lambda: credit / rng.integers(12, 40, n),
        "AMT_GOODS_PRICE": lambda: credit * rng.uniform(0.8, 1.0, n),
        "NAME_TYPE_SUITE": lambda: cat(
            np.array(["Unaccompanied", "Family", "Children", "Spouse, partner"]), n
        ),
        "NAME_INCOME_TYPE": lambda: np.where(
            pensioner, "Pensioner", cat(np.array(["Working", "Commercial associate"]), n)
        ),
        "NAME_EDUCATION_TYPE": lambda: cat(np.array(["Secondary", "Higher education"]), n),
        "NAME_FAMILY_STATUS": lambda: cat(
            np.array(["Married", "Single", "Widow", 'Civil "union", pending']), n
        ),
        "NAME_HOUSING_TYPE": lambda: cat(np.array(["House / apartment", "With parents"]), n),
        "OCCUPATION_TYPE": lambda: cat(np.array(["Laborers", "Sales staff", "Core staff"]), n),
        "ORGANIZATION_TYPE": lambda: org,
        "WEEKDAY_APPR_PROCESS_START": lambda: cat(
            np.array(["MONDAY", "TUESDAY", "FRIDAY", "SUNDAY"]), n
        ),
        "HOUR_APPR_PROCESS_START": lambda: rng.integers(0, 24, n),
        "REGION_POPULATION_RELATIVE": lambda: rng.uniform(0.001, 0.07, n),
        "REGION_RATING_CLIENT": lambda: rng.integers(1, 4, n),
        "REGION_RATING_CLIENT_W_CITY": lambda: rng.integers(1, 4, n),
        "DAYS_BIRTH": lambda: -rng.integers(7500, 25200, n),
        "DAYS_EMPLOYED": lambda: employed,
        "DAYS_REGISTRATION": lambda: -rng.uniform(0, 15000, n),
        "DAYS_ID_PUBLISH": lambda: -rng.integers(0, 6000, n),
        "DAYS_LAST_PHONE_CHANGE": lambda: -rng.integers(0, 4000, n).astype(float),
        "OWN_CAR_AGE": lambda: rng.integers(0, 30, n).astype(float),
        "CNT_FAM_MEMBERS": lambda: rng.integers(1, 6, n).astype(float),
        "EXT_SOURCE_1": lambda: ext[0],
        "EXT_SOURCE_2": lambda: ext[1],
        "EXT_SOURCE_3": lambda: ext[2],
        "EMERGENCYSTATE_MODE": lambda: cat(np.array(["Yes", "No"]), n),
    }
    app_nulls = {
        "EXT_SOURCE_1": 0.3,
        "EXT_SOURCE_3": 0.1,
        "OWN_CAR_AGE": 0.65,
        "OCCUPATION_TYPE": 0.3,
        "NAME_TYPE_SUITE": 0.01,
        "AMT_GOODS_PRICE": 0.01,
        "AMT_ANNUITY": 0.005,
        "CNT_FAM_MEMBERS": 0.002,
        **{
            f"AMT_REQ_CREDIT_BUREAU_{s}": 0.1 for s in ("HOUR", "DAY", "WEEK", "MON", "QRT", "YEAR")
        },
    }
    app_cols = schema.columns("application_train")
    app_full = _build_table(
        rng, "application", [c for c in app_cols if c.name != "TARGET"], n, app_rules, app_nulls
    )

    # ---- bureau and bureau_balance -----------------------------------------------------------
    n_bur = np.where(rng.random(n) < 0.2, 0, rng.poisson(3.0, n))
    b_total = int(n_bur.sum())
    b_app = np.repeat(np.arange(n), n_bur)
    b_ids = 5_000_000 + np.cumsum(rng.integers(1, 6, b_total))
    b_days = -rng.integers(1, 2900, b_total)
    b_closed = rng.random(b_total) < 0.6
    b_overdue = np.where(rng.random(b_total) < 0.05, rng.integers(1, 60, b_total), 0)
    b_rules: dict[str, Rule] = {
        "SK_ID_CURR": lambda: app_ids[b_app],
        "SK_ID_BUREAU": lambda: b_ids,
        "CREDIT_ACTIVE": lambda: np.where(
            b_closed,
            "Closed",
            cat(np.array(["Active", "Sold", "Bad debt"]), b_total, p=[0.9, 0.08, 0.02]),
        ),
        "CREDIT_CURRENCY": lambda: np.full(b_total, "currency 1"),
        "DAYS_CREDIT": lambda: b_days,
        "CREDIT_DAY_OVERDUE": lambda: b_overdue,
        "DAYS_CREDIT_ENDDATE": lambda: b_days + rng.integers(30, 1500, b_total).astype(float),
        "DAYS_ENDDATE_FACT": lambda: np.minimum(b_days + rng.integers(30, 1500, b_total), -1.0),
        "CREDIT_TYPE": lambda: cat(
            np.array(["Consumer credit", "Credit card", "Mortgage"]), b_total
        ),
        "DAYS_CREDIT_UPDATE": lambda: -rng.integers(0, 1000, b_total),
        "CNT_CREDIT_PROLONG": lambda: rng.poisson(0.05, b_total),
        "AMT_CREDIT_SUM_OVERDUE": lambda: np.where(
            b_overdue > 0, rng.lognormal(7, 1, b_total), 0.0
        ),
    }
    b_nulls = {
        "DAYS_CREDIT_ENDDATE": 0.06,
        "DAYS_ENDDATE_FACT": 0.35,
        "AMT_CREDIT_MAX_OVERDUE": 0.65,
        "AMT_CREDIT_SUM_DEBT": 0.15,
        "AMT_CREDIT_SUM_LIMIT": 0.35,
        "AMT_ANNUITY": 0.7,
    }
    tables["bureau"] = _build_table(
        rng, "bureau", schema.columns("bureau"), b_total, b_rules, b_nulls
    )
    # DAYS_ENDDATE_FACT is a realized event: only closed credits have it.
    fact = tables["bureau"]["DAYS_ENDDATE_FACT"]
    fact[~b_closed] = ""

    bb_counts = np.where(rng.random(b_total) < 0.1, 0, rng.integers(1, 25, b_total))
    bb_total = int(bb_counts.sum())
    bb_bur = np.repeat(np.arange(b_total), bb_counts)
    bb_rules: dict[str, Rule] = {
        "SK_ID_BUREAU": lambda: b_ids[bb_bur],
        # 0 = snapshot at application (as in the real data), -1 = freshest earlier month
        "MONTHS_BALANCE": lambda: 1 - _group_positions(bb_counts),
        "STATUS": lambda: cat(
            np.array(["C", "X", "0", "1", "2", "3", "4", "5"]),
            bb_total,
            p=[0.3, 0.2, 0.38, 0.05, 0.03, 0.02, 0.01, 0.01],
        ),
    }
    tables["bureau_balance"] = _build_table(
        rng, "bureau_balance", schema.columns("bureau_balance"), bb_total, bb_rules, {}
    )

    # ---- previous_application ----------------------------------------------------------------
    n_prev = np.where(rng.random(n) < 0.15, 0, rng.poisson(3.0, n))
    p_total = int(n_prev.sum())
    p_app = np.repeat(np.arange(n), n_prev)
    p_ids = 1_000_000 + np.cumsum(rng.integers(1, 6, p_total))
    p_status = cat(
        np.array(["Approved", "Refused", "Canceled", "Unused offer"]),
        p_total,
        p=[0.62, 0.17, 0.19, 0.02],
    )
    p_approved = p_status == "Approved"
    p_decision = -rng.integers(1, 2900, p_total)
    p_credit = rng.lognormal(10.5, 1.0, p_total)

    def p_day(offset_hi: int, ongoing: float = 0.0) -> Arr:
        vals = (p_decision + rng.integers(1, offset_hi, p_total)).astype(float)
        vals = np.minimum(vals, -1.0)
        sentinel = ~p_approved | (rng.random(p_total) < ongoing)
        return np.where(sentinel, float(SENTINEL_DAYS), vals)

    p_rules: dict[str, Rule] = {
        "SK_ID_PREV": lambda: p_ids,
        "SK_ID_CURR": lambda: app_ids[p_app],
        "NAME_CONTRACT_TYPE": lambda: cat(
            np.array(["Cash loans", "Consumer loans", "Revolving loans", "XNA"]),
            p_total,
            p=[0.44, 0.45, 0.1, 0.01],
        ),
        "AMT_APPLICATION": lambda: p_credit * rng.uniform(0.8, 1.2, p_total),
        "AMT_CREDIT": lambda: p_credit,
        "WEEKDAY_APPR_PROCESS_START": lambda: cat(
            np.array(["MONDAY", "SATURDAY", "FRIDAY"]), p_total
        ),
        "HOUR_APPR_PROCESS_START": lambda: rng.integers(0, 24, p_total),
        "FLAG_LAST_APPL_PER_CONTRACT": lambda: cat(np.array(["Y", "N"]), p_total, p=[0.99, 0.01]),
        "NFLAG_LAST_APPL_IN_DAY": lambda: rng.integers(0, 2, p_total),
        "NAME_CASH_LOAN_PURPOSE": lambda: cat(
            np.array(["XAP", "XNA", "Repairs"]), p_total, p=[0.55, 0.4, 0.05]
        ),
        "NAME_CONTRACT_STATUS": lambda: p_status,
        "DAYS_DECISION": lambda: p_decision,
        "NAME_PAYMENT_TYPE": lambda: cat(
            np.array(["Cash through the bank", "XNA", "Non-cash from your account"]),
            p_total,
            p=[0.6, 0.38, 0.02],
        ),
        "CODE_REJECT_REASON": lambda: np.where(
            p_status == "Refused", "HC", cat(np.array(["XAP", "SCO"]), p_total, p=[0.95, 0.05])
        ),
        "NAME_CLIENT_TYPE": lambda: cat(
            np.array(["Repeater", "New", "Refreshed", "XNA"]), p_total, p=[0.7, 0.2, 0.09, 0.01]
        ),
        "NAME_GOODS_CATEGORY": lambda: cat(np.array(["XNA", "Mobile", "Furniture"]), p_total),
        "NAME_PORTFOLIO": lambda: cat(np.array(["POS", "Cash", "Cards", "XNA"]), p_total),
        "NAME_PRODUCT_TYPE": lambda: cat(np.array(["XNA", "x-sell", "walk-in"]), p_total),
        "CHANNEL_TYPE": lambda: cat(
            np.array(["Country-wide", "Credit and cash offices", "Stone"]), p_total
        ),
        "SELLERPLACE_AREA": lambda: np.where(
            rng.random(p_total) < 0.3, -1, rng.integers(0, 4000, p_total)
        ),
        "NAME_SELLER_INDUSTRY": lambda: cat(
            np.array(["XNA", "Consumer electronics", "Connectivity"]), p_total
        ),
        "CNT_PAYMENT": lambda: rng.integers(4, 48, p_total).astype(float),
        "NAME_YIELD_GROUP": lambda: cat(np.array(["XNA", "middle", "high", "low_normal"]), p_total),
        "DAYS_FIRST_DRAWING": lambda: p_day(60, 0.6),
        "DAYS_FIRST_DUE": lambda: p_day(60),
        "DAYS_LAST_DUE_1ST_VERSION": lambda: p_day(900, 0.2),
        "DAYS_LAST_DUE": lambda: p_day(900, 0.3),
        "DAYS_TERMINATION": lambda: p_day(900, 0.3),
        "NFLAG_INSURED_ON_APPROVAL": lambda: rng.integers(0, 2, p_total).astype(float),
    }
    p_nulls = {
        "AMT_ANNUITY": 0.2,
        "AMT_GOODS_PRICE": 0.2,
        "AMT_DOWN_PAYMENT": 0.5,
        "RATE_DOWN_PAYMENT": 0.5,
        "RATE_INTEREST_PRIMARY": 0.99,
        "RATE_INTEREST_PRIVILEGED": 0.99,
        "NAME_TYPE_SUITE": 0.5,
        "CNT_PAYMENT": 0.2,
        "PRODUCT_COMBINATION": 0.01,
        "DAYS_FIRST_DRAWING": 0.05,
        "DAYS_FIRST_DUE": 0.05,
        "DAYS_LAST_DUE_1ST_VERSION": 0.05,
        "DAYS_LAST_DUE": 0.05,
        "DAYS_TERMINATION": 0.05,
        "NFLAG_INSURED_ON_APPROVAL": 0.4,
    }
    tables["previous_application"] = _build_table(
        rng,
        "previous_application",
        schema.columns("previous_application"),
        p_total,
        p_rules,
        p_nulls,
    )

    # ---- children of previous_application ----------------------------------------------------
    def child(prob: float, lo: int, hi: int) -> tuple[Arr, Arr]:
        counts = np.where(rng.random(p_total) < prob, rng.integers(lo, hi, p_total), 0)
        return counts, np.repeat(np.arange(p_total), counts)

    pos_counts, pos_prev = child(0.6, 1, 13)
    pos_total = len(pos_prev)
    pos_rules: dict[str, Rule] = {
        "SK_ID_PREV": lambda: p_ids[pos_prev],
        "SK_ID_CURR": lambda: app_ids[p_app[pos_prev]],
        "MONTHS_BALANCE": lambda: -_group_positions(pos_counts),
        "CNT_INSTALMENT": lambda: rng.integers(4, 48, pos_total).astype(float),
        "CNT_INSTALMENT_FUTURE": lambda: rng.integers(0, 48, pos_total).astype(float),
        "NAME_CONTRACT_STATUS": lambda: cat(
            np.array(["Active", "Completed", "Signed", "XNA"]), pos_total, p=[0.8, 0.15, 0.04, 0.01]
        ),
        "SK_DPD": lambda: np.where(
            rng.random(pos_total) < 0.05, rng.integers(1, 100, pos_total), 0
        ),
        "SK_DPD_DEF": lambda: np.where(
            rng.random(pos_total) < 0.03, rng.integers(1, 50, pos_total), 0
        ),
    }
    tables["pos_cash_balance"] = _build_table(
        rng,
        "pos_cash_balance",
        schema.columns("pos_cash_balance"),
        pos_total,
        pos_rules,
        {"CNT_INSTALMENT": 0.003, "CNT_INSTALMENT_FUTURE": 0.003},
    )

    cc_counts, cc_prev = child(0.15, 1, 10)
    cc_total = len(cc_prev)
    cc_rules: dict[str, Rule] = {
        "SK_ID_PREV": lambda: p_ids[cc_prev],
        "SK_ID_CURR": lambda: app_ids[p_app[cc_prev]],
        "MONTHS_BALANCE": lambda: -_group_positions(cc_counts),
        "NAME_CONTRACT_STATUS": lambda: cat(
            np.array(["Active", "Completed", "Signed"]), cc_total, p=[0.9, 0.08, 0.02]
        ),
    }
    cc_nulls = {
        n_: 0.2
        for n_ in (
            "AMT_DRAWINGS_ATM_CURRENT",
            "AMT_DRAWINGS_OTHER_CURRENT",
            "AMT_DRAWINGS_POS_CURRENT",
            "AMT_INST_MIN_REGULARITY",
            "AMT_PAYMENT_CURRENT",
            "CNT_DRAWINGS_ATM_CURRENT",
            "CNT_DRAWINGS_OTHER_CURRENT",
            "CNT_DRAWINGS_POS_CURRENT",
            "CNT_INSTALMENT_MATURE_CUM",
        )
    }
    tables["credit_card_balance"] = _build_table(
        rng,
        "credit_card_balance",
        schema.columns("credit_card_balance"),
        cc_total,
        cc_rules,
        cc_nulls,
    )

    inst_counts, inst_prev = child(0.6, 1, 13)
    inst_total = len(inst_prev)
    inst_num = _group_positions(inst_counts)
    inst_len = np.repeat(inst_counts, inst_counts)
    inst_start = np.repeat(rng.integers(1, 2000, p_total), inst_counts)
    inst_due = -(inst_start + (inst_len - inst_num) * 30).astype(float)
    inst_delay = rng.integers(-5, 12, inst_total)
    # Late payments are rarer for good applicants (dependence on the applicant's quality).
    inst_delay = np.where(
        rng.random(inst_total) < quality[p_app[inst_prev]] * 0.6,
        np.minimum(inst_delay, 0),
        inst_delay,
    )
    inst_paid_day = np.minimum(inst_due + inst_delay, 0.0)
    inst_amt = rng.lognormal(9.0, 1.0, inst_total)
    inst_rules: dict[str, Rule] = {
        "SK_ID_PREV": lambda: p_ids[inst_prev],
        "SK_ID_CURR": lambda: app_ids[p_app[inst_prev]],
        "NUM_INSTALMENT_VERSION": lambda: rng.integers(0, 3, inst_total).astype(float),
        "NUM_INSTALMENT_NUMBER": lambda: inst_num,
        "DAYS_INSTALMENT": lambda: inst_due,
        "DAYS_ENTRY_PAYMENT": lambda: inst_paid_day,
        "AMT_INSTALMENT": lambda: inst_amt,
        "AMT_PAYMENT": lambda: inst_amt * rng.uniform(0.7, 1.05, inst_total),
    }
    tables["installments_payments"] = _build_table(
        rng,
        "installments_payments",
        schema.columns("installments_payments"),
        inst_total,
        inst_rules,
        {"DAYS_ENTRY_PAYMENT": 0.01, "AMT_PAYMENT": 0.01},
    )

    # ---- TARGET: learnable from EXT_SOURCE_* and a few history fields ------------------------
    ext_mean = np.mean(ext, axis=0)
    late = np.maximum(inst_paid_day - inst_due, 0.0)
    inst_app = p_app[inst_prev]
    late_sum = np.bincount(inst_app, weights=late, minlength=n)
    late_cnt = np.bincount(inst_app, minlength=n)
    late_mean = np.divide(late_sum, late_cnt, out=np.zeros(n), where=late_cnt > 0)
    overdue_cnt = np.bincount(b_app, weights=(b_overdue > 0).astype(float), minlength=n)
    score = (
        -1.0 * _zscore(ext_mean)
        + 0.3 * _zscore(np.log(credit / income))
        + 0.4 * _zscore(late_mean)
        + 0.3 * _zscore(overdue_cnt)
    )
    intercept = _intercept_for_prevalence(score, TARGET_PREVALENCE)
    prob = 1 / (1 + np.exp(-(intercept + score)))
    target = (rng.random(n) < prob).astype(np.int64)

    # ---- split and write ---------------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    train = {k: v[:n_train] for k, v in app_full.items()}
    train["TARGET"] = target[:n_train].astype(str)
    test = {k: v[n_train:] for k, v in app_full.items()}
    tables["application_train"] = {c.name: train[c.name] for c in app_cols}
    tables["application_test"] = {c.name: test[c.name] for c in schema.columns("application_test")}
    for table in CORE_TABLES:
        names = [c.name for c in schema.columns(table)]
        data = tables[table]
        _write_csv(out_dir / csv_names[table], names, [data[c] for c in names])
        counts[table] = len(data[names[0]])
    return counts
