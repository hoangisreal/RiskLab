from typing import Any

import pytest

from risklab.config import ConfigError, RawSchema, load_model
from risklab.data.validate import (
    ApplicationDomains,
    KnownException,
    RangeRule,
    ValidationRules,
    check_rules_against_schema,
    resolve_rule_columns,
)
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.unit


def shipped() -> tuple[ValidationRules, ApplicationDomains]:
    return (
        load_model(CONFIG_DIR, "application_validation.yaml", ValidationRules),
        load_model(CONFIG_DIR, "application_domains.yaml", ApplicationDomains),
    )


def test_shipped_configs_match_the_schema(raw_schema: RawSchema) -> None:
    rules, domains = shipped()
    check_rules_against_schema(rules, domains, raw_schema)
    assert "XNA" in domains.domains["CODE_GENDER"]
    assert "XNA" in domains.domains["ORGANIZATION_TYPE"]


def test_flag_prefix_selects_numeric_flags_only(raw_schema: RawSchema) -> None:
    rules, _ = shipped()
    rule = next(r for r in rules.ranges if r.name == "flag_binary")
    cols = resolve_rule_columns(rule, raw_schema)
    assert "FLAG_DOCUMENT_3" in cols
    assert "FLAG_OWN_CAR" not in cols  # VARCHAR Y/N, covered by the categorical domain


def test_unknown_domain_column_and_missing_domain_are_rejected(raw_schema: RawSchema) -> None:
    rules, domains = shipped()
    extra = ApplicationDomains(domains={**domains.domains, "AMT_CREDIT": ["x"]})
    with pytest.raises(ConfigError, match="unknown"):
        check_rules_against_schema(rules, extra, raw_schema)
    missing = ApplicationDomains(
        domains={k: v for k, v in domains.domains.items() if k != "CODE_GENDER"}
    )
    with pytest.raises(ConfigError, match="CODE_GENDER"):
        check_rules_against_schema(rules, missing, raw_schema)


@pytest.mark.parametrize(
    "rule",
    [
        {"name": "r", "columns": ["NOPE"], "ge": 0},
        {"name": "r", "columns": ["CODE_GENDER"], "ge": 0},
        {"name": "r", "prefix": "ZZZ_", "ge": 0},
    ],
)
def test_bad_rule_columns_are_rejected(raw_schema: RawSchema, rule: dict[str, Any]) -> None:
    rules = ValidationRules(ranges=[RangeRule.model_validate(rule)])
    _, domains = shipped()
    with pytest.raises(ConfigError):
        check_rules_against_schema(rules, domains, raw_schema)


def test_shipped_known_exception_is_the_single_reviewed_row() -> None:
    rules, _ = shipped()
    assert [(e.table, e.check, e.ids) for e in rules.known_exceptions] == [
        ("application_test", "range:region_rating", [224393])
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {"table": "application_test", "check": "c", "ids": [], "reason": "r"},
        {"table": "application_test", "check": "c", "ids": [1, 1], "reason": "r"},
        {"table": "application_test", "check": "c", "ids": [1], "reason": ""},
        {"table": "application_test", "check": "c", "ids": [1], "reason": "  "},
        {"table": "other", "check": "c", "ids": [1], "reason": "r"},
        {"table": "application_test", "check": "c", "ids": [1], "reason": "r", "extra": 1},
        {"table": "application_test", "check": "c", "ids": ["x"], "reason": "r"},
    ],
)
def test_malformed_known_exceptions_fail_model_validation(entry: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        KnownException.model_validate(entry)


def test_duplicate_exception_entries_are_rejected() -> None:
    entry = {"table": "application_test", "check": "range:region_rating", "ids": [1], "reason": "r"}
    with pytest.raises(ValueError, match="at most one"):  # noqa: PT011
        ValidationRules.model_validate(
            {"ranges": [], "known_exceptions": [entry, {**entry, "ids": [2]}]}
        )


@pytest.mark.parametrize("check", ["id_duplicate", "range:nope", "sentinel_count:DAYS_EMPLOYED"])
def test_exception_for_unknown_or_structural_check_is_rejected(
    raw_schema: RawSchema, check: str
) -> None:
    rules, domains = shipped()
    bad = rules.model_copy(
        update={
            "known_exceptions": [
                KnownException(table="application_test", check=check, ids=[1], reason="r")
            ]
        }
    )
    with pytest.raises(ConfigError, match="not a rule check"):
        check_rules_against_schema(bad, domains, raw_schema)


@pytest.mark.parametrize(
    "rule",
    [
        {"name": "r", "columns": ["A"], "prefix": "A"},
        {"name": "r"},
        {"name": "r", "columns": ["A"]},
        {"name": "r", "columns": ["A"], "ge": 0, "values": [1]},
        {"name": "r", "columns": ["A"], "ge": float("nan")},
        {"name": "r", "columns": ["A"], "values": [float("inf")]},
        {"name": "r", "columns": ["A"], "ge": 0, "extra": 1},
    ],
)
def test_malformed_rules_fail_model_validation(rule: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        RangeRule.model_validate(rule)
