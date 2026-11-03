"""Versioned monitoring profiles use Train features and Selection outputs, with no labels."""

import numpy as np
import pandas as pd
import pytest

from risklab.monitoring.profile import ReferenceProfile, make_profile, profile_drift

pytestmark = pytest.mark.unit


def profile(train, scores=None):
    return make_profile(
        train,
        np.linspace(0.01, 0.5, 200) if scores is None else scores,
        model_sha256="a" * 64,
        contract_version="1.1",
        bins=10,
        cdf_points=256,
    )


def drift(reference, frame, scores=None):
    return profile_drift(
        reference,
        frame,
        np.linspace(0.01, 0.5, len(frame)) if scores is None else scores,
        psi_warn=0.1,
        psi_alert=0.25,
        ks_alert=0.2,
        missing_rate_alert=0.2,
    )


def test_profile_round_trip_and_reference_roles():
    train = pd.DataFrame({"x": np.linspace(0, 1, 1000), "category": ["a", "b"] * 500})
    ref = profile(train)
    restored = ReferenceProfile.model_validate_json(ref.model_dump_json())
    assert drift(restored, train)["alert_features"] == []
    altered_score_reference = profile(train, np.linspace(0.5, 1, 200))
    assert altered_score_reference.features == ref.features
    assert altered_score_reference.scores != ref.scores
    assert ref.train_rows == 1000 and ref.selection_rows == 200


@pytest.mark.parametrize("scenario", ["missing", "numeric_shift", "new_category", "absent"])
def test_frozen_reference_detects_faults(scenario):
    train = pd.DataFrame({"x": np.linspace(0, 1, 1000), "category": ["a", "b"] * 500})
    ref = profile(train)
    batch = train.copy()
    field = "x"
    if scenario == "missing":
        batch["x"] = np.nan
    elif scenario == "numeric_shift":
        batch["x"] += 2
    elif scenario == "absent":
        batch = batch.drop(columns="x")
    else:
        batch["category"] = "renamed"
        field = "category"
    assert field in drift(ref, batch)["alert_features"]


def test_constant_reference_keeps_shifted_tail_mass():
    train = pd.DataFrame({"x": np.zeros(1000)})
    ref = profile(train)
    assert "x" in drift(ref, train.assign(x=-1))["alert_features"]
    assert "x" in drift(ref, train.assign(x=1))["alert_features"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("probabilities", [float("nan")]),
        ("probabilities", [1.1, -0.1]),
        ("edges", [1.0, 0.0]),
        ("cdf_probabilities", [1.0, 0.0]),
    ],
)
def test_corrupt_reference_cannot_load(field, value):
    ref = profile(pd.DataFrame({"x": np.linspace(0, 1, 1000)})).model_dump()
    ref["features"]["x"][field] = value
    with pytest.raises(ValueError):
        ReferenceProfile.model_validate(ref)
