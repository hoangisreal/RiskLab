"""Leakage-safe preprocessing + estimator pipelines for the application model.

Each factory returns one sklearn ``Pipeline`` (steps ``prep`` and ``model``). Every cross-applicant
statistic (medians, means, scales, category sets) is learned inside ``.fit`` from whatever rows are
passed to it, which must be the Train partition only. ``transform``/``predict_proba`` never change
fitted state. Neither pipeline uses class weighting or resampling.

Logistic Regression: monetary columns get ``log1p`` (negatives clipped to 0, NaN kept) before the
median imputer (``add_indicator=True``, so missingness is a feature) and the standard scaler.
Categoricals are one-hot encoded with NULL as its own category; categories rarer than
``onehot_min_frequency`` in Train, and categories unseen at transform time, share one "infrequent"
column. Scaling also covers the missing indicators; that is harmless and keeps one code path.

LightGBM: numeric columns pass through (LightGBM handles NaN natively). Categoricals are cast to
pandas ``category`` dtype by ``CategoryCaster`` with the category set learned on Train, and
LightGBM treats ``category`` columns as categorical. Trade-offs: native categorical splits avoid
the width of one-hot but can overfit rare levels, so levels seen in fewer than
``lgbm_min_category_count`` Train rows are treated as missing. A category unseen at transform time
becomes NaN (missing) rather than raising or shifting codes, because the categories are fixed at
fit and LightGBM stores them with the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from pydantic import Field
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from risklab.config import _Strict, load_model
from risklab.features.application import (
    FeatureColumns,
    FeaturesConfig,
    feature_spec,
    load_features_config,
)


class LRParams(_Strict):
    """Logistic Regression settings (``model_lr.yaml``)."""

    seed: int
    C: float = Field(gt=0)
    max_iter: int = Field(ge=1)
    solver: Literal["lbfgs", "newton-cg", "newton-cholesky", "sag", "saga"] = "lbfgs"


class LGBMParams(_Strict):
    """LightGBM settings (``model_lgbm.yaml``)."""

    seed: int
    n_estimators: int = Field(ge=1)
    learning_rate: float = Field(gt=0)
    num_leaves: int = Field(ge=2)
    min_child_samples: int = Field(ge=1)
    subsample: float = Field(gt=0, le=1)
    subsample_freq: int = Field(ge=0)
    colsample_bytree: float = Field(gt=0, le=1)
    reg_lambda: float = Field(ge=0)
    n_jobs: int = Field(ge=1)


def load_lr_params(config_dir: Path | str | None = None) -> LRParams:
    return load_model(config_dir, "model_lr.yaml", LRParams)


def load_lgbm_params(config_dir: Path | str | None = None) -> LGBMParams:
    return load_model(config_dir, "model_lgbm.yaml", LGBMParams)


def _log1p_nonneg(X: object) -> np.ndarray:
    """log1p of values clipped at 0; NaN stays NaN."""
    out: np.ndarray = np.log1p(np.clip(np.asarray(X, dtype="float64"), 0.0, None))
    return out


class CategoryCaster(BaseEstimator, TransformerMixin):
    """Cast object columns to pandas ``category`` with Train-learned, fixed categories.

    Levels seen in fewer than ``min_count`` fitted rows are dropped (treated as missing).
    Unseen or dropped levels and NaN map to NaN at transform time.
    """

    def __init__(self, min_count: int = 1) -> None:
        self.min_count = min_count

    def fit(self, X: pd.DataFrame, y: object = None) -> CategoryCaster:
        frame = pd.DataFrame(X)
        self.feature_names_in_ = np.asarray(frame.columns, dtype=object)
        self.categories_: list[list[str]] = []
        for name in frame.columns:
            counts = frame[name].value_counts(dropna=True)
            self.categories_.append(sorted(counts[counts >= self.min_count].index.tolist()))
        return self

    @staticmethod
    def _cast(col: pd.Series, cats: list[str]) -> pd.Series:
        # Blank out unseen levels first; casting them directly is deprecated in pandas.
        col = col.astype(object)
        return col.where(col.isin(cats)).astype(pd.CategoricalDtype(categories=cats))

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        frame = pd.DataFrame(X)
        out = {
            name: self._cast(frame[name], cats)
            for name, cats in zip(self.feature_names_in_, self.categories_, strict=True)
        }
        return pd.DataFrame(out, index=frame.index, columns=list(self.feature_names_in_))

    def get_feature_names_out(self, input_features: object = None) -> np.ndarray:
        return self.feature_names_in_.copy()


def _resolve(
    cfg: FeaturesConfig | None, columns: FeatureColumns | None, config_dir: Path | str | None
) -> tuple[FeaturesConfig, FeatureColumns]:
    cfg = cfg or load_features_config(config_dir)
    return cfg, columns or feature_spec(cfg, config_dir=config_dir)


def make_lr_pipeline(
    params: LRParams,
    cfg: FeaturesConfig | None = None,
    *,
    columns: FeatureColumns | None = None,
    config_dir: Path | str | None = None,
) -> Pipeline:
    """Unfitted LR pipeline: log1p (monetary) + impute + scale, one-hot, LogisticRegression."""
    cfg, cols = _resolve(cfg, columns, config_dir)
    log_cols = set(cols.log)
    plain = [c for c in cols.numeric if c not in log_cols]

    def numeric_steps(*, log: bool) -> Pipeline:
        steps: list[tuple[str, object]] = []
        if log:
            steps.append(
                ("log1p", FunctionTransformer(_log1p_nonneg, feature_names_out="one-to-one"))
            )
        steps.append(("impute", SimpleImputer(strategy="median", add_indicator=True)))
        steps.append(("scale", StandardScaler()))
        return Pipeline(steps)

    prep = ColumnTransformer(
        [
            ("log", numeric_steps(log=True), cols.log),
            ("num", numeric_steps(log=False), plain),
            (
                "cat",
                OneHotEncoder(
                    handle_unknown="infrequent_if_exist",
                    min_frequency=cfg.onehot_min_frequency,
                    sparse_output=False,
                ),
                cols.categorical,
            ),
        ],
        remainder="drop",
        sparse_threshold=0.0,
    )
    model = LogisticRegression(
        C=params.C, max_iter=params.max_iter, solver=params.solver, random_state=params.seed
    )
    return Pipeline([("prep", prep), ("model", model)])


def make_lgbm_pipeline(
    params: LGBMParams,
    cfg: FeaturesConfig | None = None,
    *,
    columns: FeatureColumns | None = None,
    config_dir: Path | str | None = None,
) -> Pipeline:
    """Unfitted LightGBM pipeline: numeric passthrough, category casting, LGBMClassifier."""
    cfg, cols = _resolve(cfg, columns, config_dir)
    prep = ColumnTransformer(
        [
            ("num", "passthrough", cols.numeric),
            ("cat", CategoryCaster(min_count=cfg.lgbm_min_category_count), cols.categorical),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    ).set_output(transform="pandas")
    model = LGBMClassifier(
        n_estimators=params.n_estimators,
        learning_rate=params.learning_rate,
        num_leaves=params.num_leaves,
        min_child_samples=params.min_child_samples,
        subsample=params.subsample,
        subsample_freq=params.subsample_freq,
        colsample_bytree=params.colsample_bytree,
        reg_lambda=params.reg_lambda,
        random_state=params.seed,
        n_jobs=params.n_jobs,
        deterministic=True,
        force_row_wise=True,
        verbose=-1,
    )
    return Pipeline([("prep", prep), ("model", model)])


def output_feature_names(pipeline: Pipeline) -> list[str]:
    """Names of the columns the estimator receives (requires a fitted pipeline)."""
    return [str(n) for n in pipeline.named_steps["prep"].get_feature_names_out()]


__all__ = [
    "CategoryCaster",
    "LGBMParams",
    "LRParams",
    "load_lgbm_params",
    "load_lr_params",
    "make_lgbm_pipeline",
    "make_lr_pipeline",
    "output_feature_names",
]
