import random
from typing import Any

import pyarrow as pa
import pytest

from risklab.config import ConfigError, RawSchema
from risklab.data.clean import (
    CategoryRule,
    CleaningConfig,
    CleaningError,
    SentinelRule,
    check_cleaning_config,
    clean_application,
    load_cleaning_config,
)
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.unit

SENTINEL = 365243
FLAG = "DAYS_EMPLOYED_IS_SENTINEL"
CFG = CleaningConfig(
    sentinel_to_null=[SentinelRule(column="DAYS_EMPLOYED", value=SENTINEL, flag_column=FLAG)],
    category_to_null=CategoryRule(value="XNA", columns=["CODE_GENDER", "ORGANIZATION_TYPE"]),
)
SCHEMA = pa.schema(
    [
        ("SK_ID_CURR", pa.int64()),
        ("TARGET", pa.int32()),
        ("CODE_GENDER", pa.string()),
        ("ORGANIZATION_TYPE", pa.string()),
        ("DAYS_EMPLOYED", pa.float64()),
        ("AMT_CREDIT", pa.float64()),
    ]
)
ROWS: dict[str, list[Any]] = {
    "SK_ID_CURR": [101, 102, 103, 104, 105, 106, 107],
    "TARGET": [0, 1, 0, 0, 1, None, 0],
    "CODE_GENDER": ["F", "XNA", "M", None, "XNA ", "xna", "F"],
    "ORGANIZATION_TYPE": ["XNA", "Business Entity", "XNA", "School", "XNA", "xna", None],
    "DAYS_EMPLOYED": [SENTINEL, -100.0, SENTINEL, None, -SENTINEL, 365243.5, 0.0],
    "AMT_CREDIT": [1.0, 2.0, None, 4.0, 5.0, 6.0, 7.0],
}


def table(columns: list[str] | None = None) -> pa.Table:
    cols = columns or list(ROWS)
    return pa.table({c: pa.array(ROWS[c], type=SCHEMA.field(c).type) for c in cols})


def test_known_output() -> None:
    out = clean_application(table(), CFG).to_pydict()
    assert out["SK_ID_CURR"] == ROWS["SK_ID_CURR"]
    assert out["TARGET"] == ROWS["TARGET"]
    assert out["AMT_CREDIT"] == ROWS["AMT_CREDIT"]
    # exact 'XNA' only: 'XNA ' and 'xna' are kept
    assert out["CODE_GENDER"] == ["F", None, "M", None, "XNA ", "xna", "F"]
    assert out["ORGANIZATION_TYPE"] == [None, "Business Entity", None, "School", None, "xna", None]
    # only the exact sentinel is replaced; NULL stays NULL with flag 0
    assert out["DAYS_EMPLOYED"] == [None, -100.0, None, None, -SENTINEL, 365243.5, 0.0]
    assert out[FLAG] == [1, 0, 1, 0, 0, 0, 0]


def test_schema_order_and_types_preserved() -> None:
    out = clean_application(table(), CFG)
    assert out.schema == pa.schema([*SCHEMA, pa.field(FLAG, pa.int32())])
    assert out.num_rows == len(ROWS["SK_ID_CURR"])


def test_large_string_and_integer_sentinel_columns_keep_their_types() -> None:
    t = pa.table(
        {
            "SK_ID_CURR": pa.array([1, 2], type=pa.int64()),
            "CODE_GENDER": pa.array(["XNA", "F"], type=pa.large_string()),
            "ORGANIZATION_TYPE": pa.array(["A", "XNA"], type=pa.large_string()),
            "DAYS_EMPLOYED": pa.array([SENTINEL, -5], type=pa.int64()),
        }
    )
    out = clean_application(t, CFG)
    assert out.schema.field("CODE_GENDER").type == pa.large_string()
    assert out.schema.field("DAYS_EMPLOYED").type == pa.int64()
    assert out.to_pydict()["DAYS_EMPLOYED"] == [None, -5]
    assert out[FLAG].to_pylist() == [1, 0]


def test_works_without_target_and_on_empty_table() -> None:
    cols = [c for c in ROWS if c != "TARGET"]
    out = clean_application(table(cols), CFG)
    assert out.column_names == [*cols, FLAG]
    empty = clean_application(table(cols).slice(0, 0), CFG)
    assert empty.num_rows == 0
    assert empty.schema == out.schema


def test_single_row_equals_row_of_full_result() -> None:
    full = clean_application(table(), CFG)
    for i in range(table().num_rows):
        assert clean_application(table().slice(i, 1), CFG).to_pylist() == [full.to_pylist()[i]]


def test_ids_and_target_untouched_even_when_identical_to_sentinel() -> None:
    t = pa.table(
        {
            "SK_ID_CURR": pa.array([SENTINEL, 2], type=pa.int64()),
            "TARGET": pa.array([SENTINEL, None], type=pa.int64()),
            "CODE_GENDER": pa.array(["XNA", "F"]),
            "ORGANIZATION_TYPE": pa.array(["A", "B"]),
            "DAYS_EMPLOYED": pa.array([SENTINEL, 1], type=pa.int64()),
        }
    )
    out = clean_application(t, CFG)
    assert out["SK_ID_CURR"].equals(t["SK_ID_CURR"])
    assert out["TARGET"].equals(t["TARGET"])


@pytest.mark.parametrize("seed", range(5))
def test_row_wise_property(seed: int) -> None:
    """Cleaning the whole table equals cleaning chunks separately: no cross-row dependence."""
    rng = random.Random(seed)
    n = rng.randint(0, 60)
    chunk = rng.randint(1, 9)
    t = pa.table(
        {
            "SK_ID_CURR": pa.array(range(n), type=pa.int64()),
            "TARGET": pa.array([rng.choice([0, 1]) for _ in range(n)], type=pa.int32()),
            "CODE_GENDER": pa.array(
                [rng.choice(["F", "M", "XNA", None, "xna"]) for _ in range(n)], type=pa.string()
            ),
            "ORGANIZATION_TYPE": pa.array(
                [rng.choice(["XNA", "School", None, "XNA "]) for _ in range(n)], type=pa.string()
            ),
            "DAYS_EMPLOYED": pa.array(
                [rng.choice([float(SENTINEL), -1.0, 0.0, None, -2000.0]) for _ in range(n)],
                type=pa.float64(),
            ),
        }
    )
    whole = clean_application(t, CFG)
    parts = [clean_application(t.slice(i, chunk), CFG) for i in range(0, n, chunk)]
    pieces = pa.concat_tables(parts) if parts else whole.slice(0, 0)
    assert pieces.equals(whole)
    assert whole["SK_ID_CURR"].to_pylist() == list(range(n))


def test_missing_column_and_existing_flag_raise() -> None:
    with pytest.raises(CleaningError, match="missing columns"):
        clean_application(table(["SK_ID_CURR", "CODE_GENDER"]), CFG)
    with_flag = table().append_column(FLAG, pa.array([0] * 7, type=pa.int32()))
    with pytest.raises(CleaningError, match="already exist"):
        clean_application(with_flag, CFG)


def test_shipped_config_is_valid(raw_schema: RawSchema) -> None:
    cfg = load_cleaning_config(CONFIG_DIR, raw_schema)
    assert cfg.sentinel_to_null[0].column == "DAYS_EMPLOYED"
    assert cfg.category_to_null is not None
    assert cfg.category_to_null.columns == ["CODE_GENDER", "ORGANIZATION_TYPE"]


@pytest.mark.parametrize(
    ("cfg", "message"),
    [
        (
            CleaningConfig(
                sentinel_to_null=[SentinelRule(column="NOPE", value=1, flag_column="F")]
            ),
            "unknown column",
        ),
        (
            CleaningConfig(category_to_null=CategoryRule(value="XNA", columns=["NOPE"])),
            "unknown column",
        ),
        (
            CleaningConfig(
                sentinel_to_null=[
                    SentinelRule(column="DAYS_EMPLOYED", value=1, flag_column="amt_credit")
                ]
            ),
            "collides",
        ),
        (
            CleaningConfig(
                sentinel_to_null=[
                    SentinelRule(column="DAYS_EMPLOYED", value=1, flag_column="F"),
                    SentinelRule(column="DAYS_BIRTH", value=1, flag_column="f"),
                ]
            ),
            "collides",
        ),
        (
            CleaningConfig(
                sentinel_to_null=[SentinelRule(column="CODE_GENDER", value=1, flag_column="F")]
            ),
            "numeric",
        ),
        (
            CleaningConfig(category_to_null=CategoryRule(value="XNA", columns=["AMT_CREDIT"])),
            "VARCHAR",
        ),
    ],
)
def test_config_validation_errors(raw_schema: RawSchema, cfg: CleaningConfig, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        check_cleaning_config(cfg, raw_schema)


def test_config_forbids_extra_keys() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        CleaningConfig.model_validate({"sentinel_to_null": [], "unknown": 1})
