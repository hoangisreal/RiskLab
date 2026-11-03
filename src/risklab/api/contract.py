"""Generate the public raw-record schema from the versioned feature/data contracts."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StrictStr,
    create_model,
    model_validator,
)

from risklab.api.config import load_api_config
from risklab.config import _Strict, load_model, load_raw_schema
from risklab.data.validate import ValidationRules, resolve_rule_columns
from risklab.features.history_clean import HistoricalConfig
from risklab.features.registry import load_registry

SOURCES = {
    "bureau": "bureau",
    "bureau_balance": "bureau_balance",
    "previous_applications": "previous_application",
    "installments": "installments_payments",
    "pos_cash": "pos_cash_balance",
    "credit_card": "credit_card_balance",
}
REFERENCE = Annotated[StrictStr, Field(min_length=1, max_length=128)]


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("expected a finite JSON number")
    return value


def _integer(value: Any) -> int | None:
    value = _number(value)
    if value is None:
        return None
    if value != int(value):
        raise ValueError("expected an integer without rounding")
    return int(value)


def _range_validator(rules: list[Any]) -> Any:
    def validate(value: Any) -> Any:
        if value is None:
            return None
        for rule in rules:
            if value in rule.allowed:
                continue
            if rule.values is not None and value not in rule.values:
                raise ValueError("number is outside the allowed domain")
            for name, compare in (
                ("gt", lambda x, b: x > b),
                ("ge", lambda x, b: x >= b),
                ("lt", lambda x, b: x < b),
                ("le", lambda x, b: x <= b),
            ):
                boundary = getattr(rule, name)
                if boundary is not None and not compare(value, boundary):
                    raise ValueError("number is outside the configured range")
        return value

    return validate


class UnavailableSource(_Strict):
    status: Literal["not_provided"]
    records: list[Any] = Field(default_factory=list, max_length=0)


class LoanRecord(_Strict):
    prev_ref: REFERENCE | None = None
    loan_ref: REFERENCE | None = None

    @model_validator(mode="after")
    def one_reference(self) -> LoanRecord:
        if (self.prev_ref is None) == (self.loan_ref is None):
            raise ValueError("provide exactly one prev_ref or standalone loan_ref")
        return self


def _field_type(raw_type: str) -> Any:
    if raw_type in ("VARCHAR", "DATE"):
        return StrictStr
    if raw_type in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT"):
        return Annotated[int, BeforeValidator(_integer)]
    return Annotated[float, BeforeValidator(_number)]


@lru_cache(maxsize=16)
def request_model(config_dir: Path) -> type[BaseModel]:
    cfg = load_api_config(config_dir)
    schema = load_raw_schema(config_dir)
    rules = load_model(config_dir, "application_validation.yaml", ValidationRules)
    history = load_model(config_dir, "historical.yaml", HistoricalConfig)
    rules_for: dict[str, list[Any]] = {}
    for rule in rules.ranges:
        for column in resolve_rule_columns(rule, schema):
            rules_for.setdefault(column, []).append(rule)
    app_fields: dict[str, Any] = {}
    for col in schema.columns("application_train"):
        if col.name in ("SK_ID_CURR", "TARGET", "CODE_GENDER"):
            continue
        value_type = _field_type(col.type)
        if col.name in rules_for:
            value_type = Annotated[
                value_type, AfterValidator(_range_validator(rules_for[col.name]))
            ]
        required = col.name in cfg.required_application_fields
        app_fields[col.name] = (
            value_type if required else value_type | None,
            Field(...) if required else None,
        )
    if set(cfg.required_application_fields) - set(app_fields):
        raise ValueError("unknown required application fields")
    application = create_model("Application", __base__=_Strict, **app_fields)
    source_fields: dict[str, Any] = {}
    for source, table in SOURCES.items():
        fields: dict[str, Any] = {}
        table_rules = history.tables[table]
        for col in schema.columns(table):
            if col.name.startswith("SK_ID_"):
                continue
            value_type = _field_type(col.type)
            required = col.name in table_rules.required_event_columns
            bounds: dict[str, Any] = {"le": 0} if col.name in table_rules.non_future_columns else {}
            fields[col.name] = (
                value_type if required else value_type | None,
                Field(..., **bounds) if required else Field(default=None, **bounds),
            )
        if source in ("bureau", "bureau_balance"):
            fields["bureau_ref"] = (REFERENCE, ...)
        if source == "previous_applications":
            fields["prev_ref"] = (REFERENCE, ...)
        base = LoanRecord if source in ("installments", "pos_cash", "credit_card") else _Strict
        record = create_model(f"{source.title().replace('_', '')}Record", __base__=base, **fields)
        provided_fields: dict[str, Any] = {
            "status": (Literal["provided"], ...),
            "records": (
                list[record],
                Field(default_factory=list, max_length=cfg.max_records_per_source),
            ),
        }
        provided = create_model(
            f"{source.title().replace('_', '')}Provided", __base__=_Strict, **provided_fields
        )
        union = Annotated[provided | UnavailableSource, Field(discriminator="status")]
        source_fields[source] = (union | None, None)
    history_model = create_model("History", __base__=_Strict, **source_fields)
    request_fields: dict[str, Any] = {
        "contract_version": (StrictStr, load_registry(config_dir).contract_version),
        "request_id": (
            Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
            | None,
            None,
        ),
        "application": (application, ...),
        "history": (history_model, Field(default_factory=history_model)),
        "explain": (bool, Field(default=False, strict=True)),
    }
    return create_model("CreditRiskRequest", __base__=_Strict, **request_fields)
