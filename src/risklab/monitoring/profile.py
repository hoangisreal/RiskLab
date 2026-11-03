"""Immutable aggregate reference distributions fitted on Train (scores on Selection)."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.pipeline import Pipeline

from risklab.config import _Strict


class Distribution(_Strict):
    kind: Literal["numeric", "categorical"]
    rows: int = Field(ge=1)
    missing_rate: float = Field(ge=0, le=1)
    edges: list[float] = Field(default_factory=list)
    probabilities: list[float] = Field(default_factory=list)
    categories: dict[str, float] = Field(default_factory=dict)
    cdf_values: list[float] = Field(default_factory=list)
    cdf_probabilities: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_distribution(self) -> Distribution:
        def probabilities(values: list[float]) -> None:
            if any(not np.isfinite(v) or not 0 <= v <= 1 for v in values):
                raise ValueError("invalid distribution probabilities")
            if values and not np.isclose(sum(values), 1, rtol=0, atol=1e-9):
                raise ValueError("distribution probabilities must sum to one")

        if self.kind == "numeric":
            if any(not np.isfinite(v) for v in self.edges + self.cdf_values):
                raise ValueError("reference boundaries must be finite")
            if self.edges != sorted(set(self.edges)) or self.cdf_values != sorted(
                set(self.cdf_values)
            ):
                raise ValueError("reference boundaries must be unique and increasing")
            if self.probabilities and len(self.probabilities) != len(self.edges) + 1:
                raise ValueError("reference bins and probabilities differ in length")
            if len(self.cdf_values) != len(self.cdf_probabilities):
                raise ValueError("reference CDF arrays differ in length")
            if any(
                not np.isfinite(v) or not 0 <= v <= 1 for v in self.cdf_probabilities
            ) or self.cdf_probabilities != sorted(self.cdf_probabilities):
                raise ValueError("invalid reference CDF")
            probabilities(self.probabilities)
            if not self.probabilities and self.missing_rate != 1:
                raise ValueError("observed numeric reference needs bins")
        else:
            probabilities(list(self.categories.values()))
            if not self.categories and self.missing_rate != 1:
                raise ValueError("observed categorical reference needs categories")
        return self


class ReferenceProfile(_Strict):
    schema_version: str
    model_sha256: str
    contract_version: str
    feature_columns: list[str]
    train_rows: int = Field(ge=1)
    selection_rows: int = Field(ge=1)
    features: dict[str, Distribution]
    scores: Distribution
    selection_metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_shape(self) -> ReferenceProfile:
        if len(self.feature_columns) != len(set(self.feature_columns)) or set(
            self.feature_columns
        ) != set(self.features):
            raise ValueError("reference profile feature columns are inconsistent")
        if (
            any(row.rows != self.train_rows for row in self.features.values())
            or self.scores.rows != self.selection_rows
        ):
            raise ValueError("reference profile row counts are inconsistent")
        return self


def numeric_distribution(values: np.ndarray, *, bins: int, cdf_points: int) -> Distribution:
    data = np.asarray(values, dtype=float)
    finite = data[np.isfinite(data)]
    if not len(data):
        raise ValueError("empty reference distribution")
    missing = 1 - len(finite) / len(data)
    if not len(finite):
        return Distribution(kind="numeric", rows=len(data), missing_rate=missing)
    # Interior boundaries plus +/- infinity at evaluation retain shifted tail mass.
    edges = np.unique(np.quantile(finite, np.linspace(0, 1, bins + 1)[1:-1])).tolist()
    if not edges:
        edges = [float(finite[0])]
    frequencies = np.histogram(finite, bins=[-np.inf, *edges, np.inf])[0] / len(finite)
    grid = np.unique(np.quantile(finite, np.linspace(0, 1, cdf_points)))
    sorted_values = np.sort(finite)
    cdf = np.searchsorted(sorted_values, grid, side="right") / len(finite)
    return Distribution(
        kind="numeric",
        rows=len(data),
        missing_rate=missing,
        edges=edges,
        probabilities=frequencies.tolist(),
        cdf_values=grid.tolist(),
        cdf_probabilities=cdf.tolist(),
    )


class ReferenceProfiler(BaseEstimator, TransformerMixin):
    """Train-only distribution fit inside an sklearn Pipeline; no target is accepted or used."""

    def __init__(self, bins: int, cdf_points: int):
        self.bins = bins
        self.cdf_points = cdf_points

    def fit(self, X: pd.DataFrame, y: Any = None) -> ReferenceProfiler:
        self.features_: dict[str, Distribution] = {}
        for name in X:
            values = X[name]
            if pd.api.types.is_numeric_dtype(values):
                self.features_[name] = numeric_distribution(
                    values.to_numpy(), bins=self.bins, cdf_points=self.cdf_points
                )
            else:
                self.features_[name] = Distribution(
                    kind="categorical",
                    rows=len(values),
                    missing_rate=float(values.isna().mean()),
                    categories={
                        str(k): float(v)
                        for k, v in values.dropna().value_counts(normalize=True).items()
                    },
                )
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        return X


def make_profile(
    train: pd.DataFrame,
    selection_scores: np.ndarray,
    *,
    model_sha256: str,
    contract_version: str,
    bins: int,
    cdf_points: int,
    selection_labels: np.ndarray | None = None,
) -> ReferenceProfile:
    from risklab.models.evaluate import compute_metrics

    fitted = Pipeline([("reference", ReferenceProfiler(bins, cdf_points))]).fit(train)
    return ReferenceProfile(
        schema_version="1",
        model_sha256=model_sha256,
        contract_version=contract_version,
        feature_columns=list(train.columns),
        train_rows=len(train),
        selection_rows=len(selection_scores),
        features=fitted.named_steps["reference"].features_,
        scores=numeric_distribution(selection_scores, bins=bins, cdf_points=cdf_points),
        selection_metrics=(
            compute_metrics(selection_labels, selection_scores)
            if selection_labels is not None
            else {}
        ),
    )


def _psi(reference: np.ndarray, actual: np.ndarray) -> float:
    epsilon = 1e-6
    r, a = np.clip(reference, epsilon, None), np.clip(actual, epsilon, None)
    r, a = r / r.sum(), a / a.sum()
    return float(np.sum((a - r) * np.log(a / r)))


def distribution_drift(reference: Distribution, values: pd.Series) -> dict[str, float]:
    if not len(values):
        raise ValueError("empty monitoring batch")
    missing = float(values.isna().mean())
    psi, ks = 0.0, 0.0
    if reference.kind == "numeric":
        data = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
        finite = data[np.isfinite(data)]
        missing = float(1 - len(finite) / len(data))
        if len(finite) and reference.probabilities:
            p = np.histogram(finite, bins=[-np.inf, *reference.edges, np.inf])[0] / len(finite)
            psi = _psi(np.array(reference.probabilities), p)
            # Fixed quantile CDF grid approximates KS; report that definition explicitly.
            cdf = np.searchsorted(np.sort(finite), reference.cdf_values, side="right") / len(finite)
            ks = float(np.max(np.abs(cdf - np.array(reference.cdf_probabilities))))
    else:
        actual = {str(k): float(v) for k, v in values.dropna().value_counts(normalize=True).items()}
        keys = sorted(set(reference.categories) | set(actual))
        if keys:
            psi = _psi(
                np.array([reference.categories.get(k, 0) for k in keys]),
                np.array([actual.get(k, 0) for k in keys]),
            )
    return {"psi": psi, "ks": ks, "missing_rate_change": missing - reference.missing_rate}


def profile_drift(
    profile: ReferenceProfile,
    batch: pd.DataFrame,
    scores: np.ndarray,
    *,
    psi_warn: float,
    psi_alert: float,
    ks_alert: float,
    missing_rate_alert: float,
) -> dict[str, Any]:
    rows = []
    for name, reference in profile.features.items():
        if name not in batch:
            rows.append(
                dict(
                    feature=name,
                    status="alert",
                    psi=0.0,
                    ks=0.0,
                    missing_rate_change=1 - reference.missing_rate,
                )
            )
            continue
        values = distribution_drift(reference, batch[name])
        alert = (
            values["psi"] >= psi_alert
            or values["ks"] >= ks_alert
            or abs(values["missing_rate_change"]) >= missing_rate_alert
        )
        rows.append(
            dict(
                feature=name,
                status="alert" if alert else ("warn" if values["psi"] >= psi_warn else "ok"),
                **values,
            )
        )
    score_drift = distribution_drift(profile.scores, pd.Series(scores))
    return {
        "drift": rows,
        "alert_features": [r["feature"] for r in rows if r["status"] == "alert"],
        "score_psi": score_drift["psi"],
        "score_alert": score_drift["psi"] >= psi_alert,
        "ks_definition": "CDF effect size on frozen Train quantile grid",
    }
