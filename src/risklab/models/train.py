"""Inner cross-validation on Train and final fits for the application models.

Everything here receives Train rows only. Hyperparameters and the number of boosting rounds are
chosen by stratified K-fold inside Train, with preprocessing refitted within every fold, so no
validation fold influences its own preprocessing statistics. Selection and Test are never seen.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, early_stopping, log_evaluation
from sklearn.base import clone
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline

from risklab.features.application import FeatureColumns, FeaturesConfig
from risklab.models.pipelines import LGBMParams, LRParams, make_lgbm_pipeline, make_lr_pipeline


@dataclass(frozen=True)
class LRTuning:
    c_grid: list[float]
    mean_auc: list[float]
    std_auc: list[float]
    best_c: float


@dataclass(frozen=True)
class LGBMTuning:
    best_iterations: list[int]
    fold_auc: list[float]
    chosen_rounds: int


def _folds(y: pd.Series, n_splits: int, seed: int) -> list[tuple[np.ndarray, np.ndarray]]:
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    return list(skf.split(np.zeros(len(y)), y.to_numpy()))


def tune_lr(
    X: pd.DataFrame,
    y: pd.Series,
    base: LRParams,
    features_cfg: FeaturesConfig,
    *,
    c_grid: list[float],
    folds: int,
    seed: int,
    config_dir: str | None = None,
    columns: FeatureColumns | None = None,
) -> LRTuning:
    """Choose C by mean inner-CV ROC-AUC on Train; ties go to the smaller (more regularised) C."""
    splits = _folds(y, folds, seed)
    means: list[float] = []
    stds: list[float] = []
    for c in c_grid:
        params = base.model_copy(update={"C": c})
        scores = []
        for tr, va in splits:
            pipe = make_lr_pipeline(params, features_cfg, columns=columns, config_dir=config_dir)
            pipe.fit(X.iloc[tr], y.iloc[tr])
            scores.append(roc_auc_score(y.iloc[va], pipe.predict_proba(X.iloc[va])[:, 1]))
        means.append(float(np.mean(scores)))
        stds.append(float(np.std(scores)))
    best = max(range(len(c_grid)), key=lambda i: (round(means[i], 12), -c_grid[i]))
    return LRTuning(c_grid=list(c_grid), mean_auc=means, std_auc=stds, best_c=c_grid[best])


def tune_lgbm_rounds(
    X: pd.DataFrame,
    y: pd.Series,
    base: LGBMParams,
    features_cfg: FeaturesConfig,
    *,
    folds: int,
    seed: int,
    max_rounds: int,
    early_stopping_rounds: int,
    config_dir: str | None = None,
    columns: FeatureColumns | None = None,
) -> LGBMTuning:
    """Number of boosting rounds from early stopping on each inner-CV fold of Train.

    Preprocessing (including the category sets) is refitted on every fold's training rows. The
    chosen number of rounds is the rounded mean of the per-fold best iterations.
    """
    best_iterations: list[int] = []
    fold_auc: list[float] = []
    for tr, va in _folds(y, folds, seed):
        pipe = make_lgbm_pipeline(
            base.model_copy(update={"n_estimators": max_rounds}),
            features_cfg,
            config_dir=config_dir,
            columns=columns,
        )
        prep: Pipeline = pipe.named_steps["prep"]
        model: LGBMClassifier = pipe.named_steps["model"]
        X_tr = prep.fit_transform(X.iloc[tr], y.iloc[tr])
        X_va = prep.transform(X.iloc[va])
        model.fit(
            X_tr,
            y.iloc[tr],
            eval_X=X_va,
            eval_y=y.iloc[va],
            eval_metric="auc",
            callbacks=[early_stopping(early_stopping_rounds, verbose=False), log_evaluation(0)],
        )
        best_iterations.append(int(model.best_iteration_))
        fold_auc.append(float(model.best_score_["valid_0"]["auc"]))
    return LGBMTuning(
        best_iterations=best_iterations,
        fold_auc=fold_auc,
        chosen_rounds=max(1, round(float(np.mean(best_iterations)))),
    )


def fit_pipeline(pipeline: Pipeline, X: pd.DataFrame, y: pd.Series) -> Pipeline:
    """Fit a fresh clone of ``pipeline`` on Train rows and return it."""
    fitted: Pipeline = clone(pipeline)
    fitted.fit(X, y)
    return fitted


__all__ = [
    "LGBMTuning",
    "LRTuning",
    "fit_pipeline",
    "tune_lgbm_rounds",
    "tune_lr",
]
