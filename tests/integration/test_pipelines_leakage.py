"""Leakage and behavior tests for the application pipelines (synthetic data, no metrics)."""

from __future__ import annotations

import pickle
from typing import Any

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from risklab.data.loaders import PartitionRole, Purpose
from risklab.features.application import (
    FeatureColumns,
    FeaturesConfig,
    feature_spec,
    load_application_frame,
    load_features_config,
)
from risklab.models.pipelines import (
    LGBMParams,
    LRParams,
    load_lgbm_params,
    load_lr_params,
    make_lgbm_pipeline,
    make_lr_pipeline,
    output_feature_names,
)
from tests.integration.conftest import AppEnv

pytestmark = pytest.mark.integration

Frames = tuple[pd.DataFrame, pd.Series]


@pytest.fixture(scope="module")
def cfg(app_env: AppEnv) -> FeaturesConfig:
    return load_features_config(app_env.config_dir)


@pytest.fixture(scope="module")
def cols(app_env: AppEnv, cfg: FeaturesConfig) -> FeatureColumns:
    return feature_spec(cfg, config_dir=app_env.config_dir)


def _load(app_env: AppEnv, role: PartitionRole, purpose: Purpose) -> Frames:
    return load_application_frame(
        role,
        purpose,
        data_cfg=app_env.data_cfg,
        partition_cfg=app_env.partition_cfg,
        config_dir=app_env.config_dir,
    )


@pytest.fixture(scope="module")
def train(app_env: AppEnv) -> Frames:
    return _load(app_env, PartitionRole.train, Purpose.fit)


@pytest.fixture(scope="module")
def selection(app_env: AppEnv) -> Frames:
    return _load(app_env, PartitionRole.selection, Purpose.select)


@pytest.fixture(scope="module")
def calibration(app_env: AppEnv) -> Frames:
    return _load(app_env, PartitionRole.calibration, Purpose.calibrate)


@pytest.fixture(scope="module")
def lr_params(app_env: AppEnv) -> LRParams:
    return load_lr_params(app_env.config_dir).model_copy(update={"max_iter": 200})


@pytest.fixture(scope="module")
def lgbm_params(app_env: AppEnv) -> LGBMParams:
    return load_lgbm_params(app_env.config_dir).model_copy(
        update={"n_estimators": 10, "min_child_samples": 20}
    )


def poisoned(X: pd.DataFrame, cols: FeatureColumns) -> pd.DataFrame:
    """A frame with shifted numerics and categories that never occur in Train."""
    out = X.copy()
    out[cols.numeric] = out[cols.numeric] * 1000 + 12345.0
    for c in cols.categorical:
        out[c] = "POISON_" + out[c].astype(str)
    return out


def make(
    kind: str,
    lr_params: LRParams,
    lgbm_params: LGBMParams,
    cfg: FeaturesConfig,
    cols: FeatureColumns,
) -> Pipeline:
    if kind == "lr":
        return make_lr_pipeline(lr_params, cfg, columns=cols)
    return make_lgbm_pipeline(lgbm_params, cfg, columns=cols)


def state(pipe: Pipeline) -> bytes:
    """Serialized fitted state (all learned attributes of every step)."""
    return pickle.dumps(pipe)


# --- (a) LR statistics equal independent Train statistics -----------------------------------


def test_lr_fitted_statistics_equal_independent_train_statistics(
    train: Frames, lr_params: LRParams, cfg: FeaturesConfig, cols: FeatureColumns
) -> None:
    X, y = train
    pipe = make_lr_pipeline(lr_params, cfg, columns=cols).fit(X, y)
    prep = pipe.named_steps["prep"]
    plain = [c for c in cols.numeric if c not in set(cols.log)]
    expected_inputs = {
        "log": np.log1p(X[cols.log].clip(lower=0)),
        "num": X[plain],
    }
    for name, frame in expected_inputs.items():
        block = prep.named_transformers_[name]
        medians = frame.median(skipna=True).to_numpy()
        assert not np.isnan(medians).any()
        np.testing.assert_allclose(block["impute"].statistics_, medians, rtol=1e-12)
        imputed = frame.fillna(frame.median())
        indicator = frame.isna().loc[:, frame.isna().any()].astype(float)
        full = pd.concat([imputed, indicator], axis=1).to_numpy()
        np.testing.assert_allclose(block["scale"].mean_, full.mean(axis=0), rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(block["scale"].scale_, full.std(axis=0, ddof=0), rtol=1e-9)

    encoder = prep.named_transformers_["cat"]
    for cat, learned in zip(cols.categorical, encoder.categories_, strict=True):
        got = {None if pd.isna(v) else v for v in learned}
        want = {None if pd.isna(v) else v for v in X[cat].unique()}
        assert got == want, cat


def test_lgbm_categories_learned_from_train_only(
    train: Frames, lgbm_params: LGBMParams, cfg: FeaturesConfig, cols: FeatureColumns
) -> None:
    X, y = train
    pipe = make_lgbm_pipeline(lgbm_params, cfg, columns=cols).fit(X, y)
    caster = pipe.named_steps["prep"].named_transformers_["cat"]
    assert list(caster.feature_names_in_) == cols.categorical
    for cat, learned in zip(cols.categorical, caster.categories_, strict=True):
        counts = X[cat].value_counts()
        assert learned == sorted(counts[counts >= cfg.lgbm_min_category_count].index)
    # Numeric columns pass through unchanged (NaN preserved).
    out = pipe.named_steps["prep"].transform(X)
    pd.testing.assert_frame_equal(out[cols.numeric], X[cols.numeric], check_names=False)


def test_lgbm_booster_registers_categorical_columns(
    train: Frames, lgbm_params: LGBMParams, cfg: FeaturesConfig, cols: FeatureColumns
) -> None:
    X, y = train
    pipe = make_lgbm_pipeline(lgbm_params, cfg, columns=cols).fit(X, y)
    caster = pipe.named_steps["prep"].named_transformers_["cat"]
    registered = pipe.named_steps["model"].booster_.pandas_categorical
    assert registered
    assert len(registered) == len(cols.categorical)
    for cat, got, want in zip(cols.categorical, registered, caster.categories_, strict=True):
        assert list(got) == list(want), cat


# --- (b) transform/predict does not change fitted state --------------------------------------


@pytest.mark.parametrize("kind", ["lr", "lgbm"])
def test_scoring_other_partitions_does_not_change_fitted_state(
    kind: str,
    train: Frames,
    selection: Frames,
    calibration: Frames,
    lr_params: LRParams,
    lgbm_params: LGBMParams,
    cfg: FeaturesConfig,
    cols: FeatureColumns,
) -> None:
    pipe = make(kind, lr_params, lgbm_params, cfg, cols).fit(*train)
    before = state(pipe)
    for X, _ in (selection, calibration):
        pipe.named_steps["prep"].transform(X)
        pipe.predict_proba(X)
    pipe.predict_proba(poisoned(selection[0], cols))
    assert state(pipe) == before


# --- (c) no hidden dependence on other partitions ---------------------------------------------


@pytest.mark.parametrize("kind", ["lr", "lgbm"])
def test_fit_ignores_other_partitions_in_memory(
    kind: str,
    train: Frames,
    selection: Frames,
    lr_params: LRParams,
    lgbm_params: LGBMParams,
    cfg: FeaturesConfig,
    cols: FeatureColumns,
) -> None:
    X, y = train
    clean = make(kind, lr_params, lgbm_params, cfg, cols).fit(X, y)

    bad_X = poisoned(selection[0], cols)  # exists in memory, scored, never fitted on
    other = make(kind, lr_params, lgbm_params, cfg, cols).fit(X.copy(), y.copy())
    other.predict_proba(bad_X)

    assert state(clean) == state(other)
    np.testing.assert_array_equal(clean.predict_proba(X), other.predict_proba(X))


@pytest.mark.parametrize("kind", ["lr", "lgbm"])
def test_fit_is_sensitive_to_its_input_rows(
    kind: str,
    train: Frames,
    selection: Frames,
    lr_params: LRParams,
    lgbm_params: LGBMParams,
    cfg: FeaturesConfig,
    cols: FeatureColumns,
) -> None:
    # Control for the test above: fitting on Train plus the poisoned rows does change the state.
    X, y = train
    bad_X = poisoned(selection[0], cols)
    mixed_X = pd.concat([X, bad_X.rename(index=lambda i: -i)])
    mixed_y = pd.concat([y, selection[1].rename(index=lambda i: -i)])
    clean = make(kind, lr_params, lgbm_params, cfg, cols).fit(X, y)
    mixed = make(kind, lr_params, lgbm_params, cfg, cols).fit(mixed_X, mixed_y)
    assert state(clean) != state(mixed)


# --- (d)/(e) unseen categories ---------------------------------------------------------------


def test_lr_unseen_category_maps_to_infrequent_or_zeros(
    train: Frames, selection: Frames, lr_params: LRParams, cfg: FeaturesConfig, cols: FeatureColumns
) -> None:
    X, y = train
    pipe = make_lr_pipeline(lr_params, cfg, columns=cols).fit(X, y)
    encoder = pipe.named_steps["prep"].named_transformers_["cat"]
    sample = selection[0].head(20).copy()
    cat = cols.categorical.index("OCCUPATION_TYPE")
    sample["OCCUPATION_TYPE"] = "NEVER_SEEN_IN_TRAIN"

    encoded = encoder.transform(sample[cols.categorical])  # must not raise
    names = list(encoder.get_feature_names_out(cols.categorical))
    block = [i for i, n in enumerate(names) if n.startswith("OCCUPATION_TYPE_")]
    got = encoded[:, block]
    assert (got == got[0]).all()  # every row identical in this block
    infrequent = encoder.infrequent_categories_[cat]
    if infrequent is not None and len(infrequent) > 0:
        assert got[0].sum() == 1
        assert names[block[int(np.argmax(got[0]))]].endswith("infrequent_sklearn")
    else:
        assert got[0].sum() == 0
    # Other columns are unaffected by the unseen value.
    other = [i for i in range(len(names)) if i not in block]
    np.testing.assert_array_equal(
        encoded[:, other], encoder.transform(selection[0].head(20)[cols.categorical])[:, other]
    )
    pipe.predict_proba(sample)


def test_lgbm_unseen_category_becomes_missing_without_shifting_codes(
    train: Frames,
    selection: Frames,
    lgbm_params: LGBMParams,
    cfg: FeaturesConfig,
    cols: FeatureColumns,
) -> None:
    X, y = train
    pipe = make_lgbm_pipeline(lgbm_params, cfg, columns=cols).fit(X, y)
    prep = pipe.named_steps["prep"]
    sample = selection[0].head(30).copy()
    base = prep.transform(sample)
    unseen = sample.copy()
    unseen["OCCUPATION_TYPE"] = "NEVER_SEEN_IN_TRAIN"
    out = prep.transform(unseen)  # must not raise
    assert out["OCCUPATION_TYPE"].isna().all()
    for c in cols.categorical:
        assert out[c].dtype == "category"
        assert list(out[c].cat.categories) == list(base[c].cat.categories)  # codes unchanged
        if c != "OCCUPATION_TYPE":
            pd.testing.assert_series_equal(out[c], base[c])
    # Unseen behaves exactly like an explicitly missing value.
    missing = sample.copy()
    missing["OCCUPATION_TYPE"] = np.nan
    np.testing.assert_array_equal(pipe.predict_proba(unseen), pipe.predict_proba(missing))


# --- smoke, determinism, feature names ---------------------------------------------------------


@pytest.mark.parametrize("kind", ["lr", "lgbm"])
def test_pipeline_smoke_and_determinism(
    kind: str,
    train: Frames,
    selection: Frames,
    lr_params: LRParams,
    lgbm_params: LGBMParams,
    cfg: FeaturesConfig,
    cols: FeatureColumns,
) -> None:
    X, y = train
    first = make(kind, lr_params, lgbm_params, cfg, cols).fit(X, y)
    second = make(kind, lr_params, lgbm_params, cfg, cols).fit(X, y)
    Xs = selection[0]
    p1 = first.predict_proba(Xs)
    assert p1.shape == (len(Xs), 2)
    assert np.isfinite(p1).all()
    assert ((p1 >= 0) & (p1 <= 1)).all()
    np.testing.assert_array_equal(p1, second.predict_proba(Xs))

    names = output_feature_names(first)
    assert len(names) == len(set(names))
    n_in = first.named_steps["model"].n_features_in_
    assert len(names) == n_in


def test_no_class_weighting(
    lr_params: LRParams, lgbm_params: LGBMParams, cfg: FeaturesConfig, cols: FeatureColumns
) -> None:
    lr = make_lr_pipeline(lr_params, cfg, columns=cols).named_steps["model"]
    gbm = make_lgbm_pipeline(lgbm_params, cfg, columns=cols).named_steps["model"]
    assert lr.class_weight is None
    assert gbm.class_weight is None
    params: dict[str, Any] = gbm.get_params()
    assert not {"scale_pos_weight", "is_unbalance"} & set(params)
    assert params["deterministic"] is True and params["force_row_wise"] is True
