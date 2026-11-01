from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from risklab.config import ConfigError, RawSchema, load_model
from risklab.data.clean import load_cleaning_config
from risklab.features.application import (
    FeaturesConfig,
    load_features_config,
    resolve_columns,
)
from risklab.models.pipelines import (
    LGBMParams,
    LRParams,
    load_lgbm_params,
    load_lr_params,
)
from tests.conftest import CONFIG_DIR

pytestmark = pytest.mark.unit


def features(**overrides: Any) -> FeaturesConfig:
    base = yaml.safe_load((CONFIG_DIR / "features_application.yaml").read_text())
    return FeaturesConfig.model_validate({**base, **overrides})


def resolve(cfg: FeaturesConfig, schema: RawSchema):  # type: ignore[no-untyped-def]
    return resolve_columns(
        cfg,
        schema,
        load_cleaning_config(CONFIG_DIR, schema),
        id_column="SK_ID_CURR",
        target_column="TARGET",
    )


def test_shipped_features_config_resolves(raw_schema: RawSchema) -> None:
    cols = resolve(load_features_config(CONFIG_DIR), raw_schema)
    assert set(cols.numeric).isdisjoint(cols.categorical)
    assert "CODE_GENDER" not in cols.numeric + cols.categorical
    assert cols.log == ["AMT_INCOME_TOTAL", "AMT_CREDIT", "AMT_ANNUITY", "AMT_GOODS_PRICE"]


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG_DIR / "features_application.yaml").read_text())
    (tmp_path / "features_application.yaml").write_text(yaml.safe_dump({**raw, "typo": 1}))
    with pytest.raises(ConfigError, match="typo"):
        load_features_config(tmp_path)


def test_nonexistent_excluded_column_is_rejected(raw_schema: RawSchema) -> None:
    cfg = features(exclude=["SK_ID_CURR", "TARGET", "NOT_A_COLUMN"])
    with pytest.raises(ConfigError, match="NOT_A_COLUMN"):
        resolve(cfg, raw_schema)


@pytest.mark.parametrize("dropped", ["SK_ID_CURR", "TARGET"])
def test_id_and_target_must_stay_excluded(raw_schema: RawSchema, dropped: str) -> None:
    exclude = [c for c in ("SK_ID_CURR", "TARGET", "CODE_GENDER") if c != dropped]
    with pytest.raises(ConfigError, match=dropped):
        resolve(features(exclude=exclude), raw_schema)


def test_excluding_sentinel_source_column_drops_its_flag(raw_schema: RawSchema) -> None:
    base = ["SK_ID_CURR", "TARGET", "CODE_GENDER"]
    cols = resolve(features(exclude=[*base, "DAYS_EMPLOYED"]), raw_schema)
    assert "DAYS_EMPLOYED" not in cols.numeric
    assert "DAYS_EMPLOYED_IS_SENTINEL" not in cols.numeric


def test_excluding_flag_name_drops_only_the_flag(raw_schema: RawSchema) -> None:
    base = ["SK_ID_CURR", "TARGET", "CODE_GENDER"]
    cols = resolve(features(exclude=[*base, "DAYS_EMPLOYED_IS_SENTINEL"]), raw_schema)
    assert "DAYS_EMPLOYED_IS_SENTINEL" not in cols.numeric
    assert "DAYS_EMPLOYED" in cols.numeric


def test_exclude_name_neither_column_nor_flag_is_rejected(raw_schema: RawSchema) -> None:
    cfg = features(exclude=["SK_ID_CURR", "TARGET", "DAYS_EMPLOYED_IS_NOPE"])
    with pytest.raises(ConfigError, match="DAYS_EMPLOYED_IS_NOPE"):
        resolve(cfg, raw_schema)


def test_log_prefix_matching_nothing_is_rejected(raw_schema: RawSchema) -> None:
    with pytest.raises(ConfigError, match="matches no numeric column"):
        resolve(features(log_prefix="NOPE_"), raw_schema)


def test_log_columns_never_include_excluded_or_categorical(raw_schema: RawSchema) -> None:
    cols = resolve(features(exclude=["SK_ID_CURR", "TARGET", "AMT_CREDIT"]), raw_schema)
    assert "AMT_CREDIT" not in cols.log
    assert set(cols.log) <= set(cols.numeric)


@pytest.mark.parametrize(
    "bad",
    [
        {"onehot_min_frequency": 0},
        {"onehot_min_frequency": 1.5},
        {"lgbm_min_category_count": 0},
        {"log_prefix": ""},
        {"exclude": ["TARGET", "TARGET"]},
    ],
)
def test_bad_feature_values_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        features(**bad)


def test_shipped_model_configs_load() -> None:
    lr, gbm = load_lr_params(CONFIG_DIR), load_lgbm_params(CONFIG_DIR)
    assert lr.seed == gbm.seed


@pytest.mark.parametrize(
    ("name", "model", "bad"),
    [
        ("model_lr.yaml", LRParams, {"unknown_key": 1}),
        ("model_lr.yaml", LRParams, {"class_weight": "balanced"}),
        ("model_lr.yaml", LRParams, {"C": 0}),
        ("model_lr.yaml", LRParams, {"solver": "magic"}),
        ("model_lr.yaml", LRParams, {"max_iter": 0}),
        ("model_lgbm.yaml", LGBMParams, {"scale_pos_weight": 5}),
        ("model_lgbm.yaml", LGBMParams, {"is_unbalance": True}),
        ("model_lgbm.yaml", LGBMParams, {"subsample": 1.5}),
        ("model_lgbm.yaml", LGBMParams, {"num_leaves": 1}),
        ("model_lgbm.yaml", LGBMParams, {"learning_rate": -0.1}),
        ("model_lgbm.yaml", LGBMParams, {"n_jobs": 0}),
    ],
)
def test_bad_model_params_are_rejected(
    tmp_path: Path, name: str, model: type[LRParams | LGBMParams], bad: dict[str, Any]
) -> None:
    raw = yaml.safe_load((CONFIG_DIR / name).read_text())
    (tmp_path / name).write_text(yaml.safe_dump({**raw, **bad}))
    with pytest.raises(ConfigError):
        load_model(tmp_path, name, model)
