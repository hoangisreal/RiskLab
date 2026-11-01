from __future__ import annotations

import pandas as pd
import pytest

from risklab.config import load_raw_schema
from risklab.data.clean import clean_application, load_cleaning_config
from risklab.data.loaders import (
    ALLOWED_PURPOSE,
    PartitionRole,
    Purpose,
    TestAccessError,
    load_partition,
)
from risklab.data.partition import read_role_ids
from risklab.features.application import (
    feature_columns,
    feature_spec,
    load_application_frame,
    load_features_config,
)
from tests.integration.conftest import AppEnv

pytestmark = pytest.mark.integration


def frame(env: AppEnv, role: PartitionRole, purpose: Purpose) -> tuple[pd.DataFrame, pd.Series]:
    return load_application_frame(
        role,
        purpose,
        data_cfg=env.data_cfg,
        partition_cfg=env.partition_cfg,
        config_dir=env.config_dir,
    )


def test_columns_are_deterministic_and_exclude_id_target_gender(app_env: AppEnv) -> None:
    X1, _ = frame(app_env, PartitionRole.train, Purpose.fit)
    X2, _ = frame(app_env, PartitionRole.selection, Purpose.select)
    assert list(X1.columns) == list(X2.columns)
    for col in ("SK_ID_CURR", "TARGET", "CODE_GENDER"):
        assert col not in X1.columns
    cfg = load_features_config(app_env.config_dir)
    numeric, categorical = feature_columns(cfg, config_dir=app_env.config_dir)
    assert list(X1.columns) == [*numeric, *categorical]


def test_numeric_and_categorical_derive_from_schema(app_env: AppEnv) -> None:
    cfg = load_features_config(app_env.config_dir)
    numeric, categorical = feature_columns(cfg, config_dir=app_env.config_dir)
    schema = load_raw_schema(app_env.config_dir)
    types = {c.name: c.type for c in schema.columns("application_train")}
    assert not set(numeric) & set(categorical)
    assert all(types[c] == "VARCHAR" for c in categorical)
    assert all(types[c] != "VARCHAR" for c in numeric if c in types)
    assert "DAYS_EMPLOYED_IS_SENTINEL" in numeric
    expected = set(types) - set(cfg.exclude) | {"DAYS_EMPLOYED_IS_SENTINEL"}
    assert set(numeric) | set(categorical) == expected
    log = feature_spec(cfg, config_dir=app_env.config_dir).log
    assert {"AMT_CREDIT", "AMT_INCOME_TOTAL"} <= set(log)
    assert not any(c.startswith("AMT_REQ_CREDIT_BUREAU_") for c in log)
    assert set(log) <= set(numeric)


@pytest.mark.parametrize(("role", "purpose"), list(ALLOWED_PURPOSE.items()))
def test_index_is_ids_aligned_with_target(
    app_env: AppEnv, role: PartitionRole, purpose: Purpose
) -> None:
    X, y = frame(app_env, role, purpose)
    ids = read_role_ids(app_env.partition_cfg.output_dir / f"{role.value}.parquet", "SK_ID_CURR")
    assert list(X.index) == ids
    assert X.index.equals(y.index)
    assert X.index.name == "SK_ID_CURR"
    assert y.dtype == "int64"
    assert set(y.unique()) <= {0, 1}


def test_dtypes_and_missing_values_are_stable(app_env: AppEnv) -> None:
    cfg = load_features_config(app_env.config_dir)
    numeric, categorical = feature_columns(cfg, config_dir=app_env.config_dir)
    for role, purpose in (
        (PartitionRole.train, Purpose.fit),
        (PartitionRole.selection, Purpose.select),
    ):
        X, _ = frame(app_env, role, purpose)
        assert all(X[c].dtype == "float64" for c in numeric)
        assert all(X[c].dtype == object for c in categorical)
        assert X[numeric].isna().any().any(), role  # NULLs are kept as NaN, not imputed
        assert X[categorical].isna().any().any(), role


def test_values_equal_row_wise_cleaned_partition(app_env: AppEnv) -> None:
    X, _ = frame(app_env, PartitionRole.train, Purpose.fit)
    table = load_partition(
        PartitionRole.train,
        Purpose.fit,
        data_cfg=app_env.data_cfg,
        partition_cfg=app_env.partition_cfg,
        config_dir=app_env.config_dir,
    )
    schema = load_raw_schema(app_env.config_dir)
    cleaned = clean_application(table, load_cleaning_config(app_env.config_dir, schema))
    raw = cleaned.to_pandas().set_index("SK_ID_CURR")
    ids = list(X.index[::37][:25])
    assert ids
    for col in X.columns:
        want = raw.loc[ids, col]
        want = want.astype("float64") if X[col].dtype == "float64" else want.astype(object)
        got = X.loc[ids, col]
        assert (got.isna().to_numpy() == want.isna().to_numpy()).all(), col
        present = ~got.isna().to_numpy()
        assert (got.to_numpy()[present] == want.to_numpy()[present]).all(), col


def test_cleaning_is_applied(app_env: AppEnv) -> None:
    X, _ = frame(app_env, PartitionRole.train, Purpose.fit)
    assert (X["DAYS_EMPLOYED"] == 365243).sum() == 0
    flag = X["DAYS_EMPLOYED_IS_SENTINEL"]
    assert set(flag.unique()) <= {0.0, 1.0}
    assert flag.sum() > 0
    assert X.loc[flag == 1, "DAYS_EMPLOYED"].isna().all()
    assert not (X["ORGANIZATION_TYPE"] == "XNA").any()


def test_test_role_raises(app_env: AppEnv) -> None:
    for purpose in Purpose:
        with pytest.raises(TestAccessError):
            frame(app_env, PartitionRole.test, purpose)
