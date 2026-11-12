"""Checksum-pinned catalog and raw snapshot validation without data/model access."""

import hashlib
import json

import pytest

from risklab.business.catalog import DemoCatalog, PinnedRelease, canonical_json
from risklab.business.errors import AppError
from risklab.business.imports import stage_upload
from risklab.business.storage import LocalStorage, StorageError
from tests.conftest import CONFIG_DIR, REPO_ROOT

pytestmark = pytest.mark.unit
FIXTURES = REPO_ROOT / "tests/fixtures/business_v1"
CSV = b"external_reference,snapshot_id,supersedes_application_id\nREF,demo-history,\n"


@pytest.fixture
def release():
    return PinnedRelease(
        model_version="synthetic-test",
        model_sha256="a" * 64,
        model_run_id="synthetic-run",
        lock_id="synthetic-lock",
        feature_contract_version="1.1",
        feature_groups=["bureau", "previous", "installments"],
    )


def catalog(document=None):
    data = (
        (FIXTURES / "catalog.json").read_bytes() if document is None else canonical_json(document)
    )
    return DemoCatalog(
        data, expected_sha256=hashlib.sha256(data).hexdigest(), config_dir=CONFIG_DIR
    )


@pytest.mark.parametrize(
    "data", [b"{", b"\xff", b'{"catalog_version":"first","catalog_version":"second"}', b"[]"]
)
def test_invalid_catalog_rejected_without_echoing_content(data):
    with pytest.raises(AppError) as error:
        DemoCatalog(data, expected_sha256=hashlib.sha256(data).hexdigest(), config_dir=CONFIG_DIR)
    assert error.value.status == 503
    assert error.value.code == "CATALOG_UNAVAILABLE"
    assert "first" not in str(error.value)


def test_checksum_corruption_unknown_groups_and_contract_mismatch():
    data = (FIXTURES / "catalog.json").read_bytes()
    with pytest.raises(AppError, match="integrity"):
        DemoCatalog(data, expected_sha256="0" * 64, config_dir=CONFIG_DIR)
    for fields in (
        {"required_groups": ["unknown"]},
        {"required_groups": ["bureau", "bureau"]},
        {"feature_contract_version": "999"},
        {"catalog_version": "../path"},
    ):
        document = json.loads(data)
        document.update(fields)
        with pytest.raises(AppError):
            catalog(document)


@pytest.mark.parametrize(
    "field,value", [("contract_version", "999"), ("explain", False), ("request_id", "not-allowed")]
)
def test_snapshot_contract_and_explanation_are_required(release, tmp_path, field, value):
    document = json.loads((FIXTURES / "catalog.json").read_text())
    document["snapshots"]["demo-history"][field] = value
    prepared = catalog(document).prepare(CSV, release, LocalStorage(tmp_path))
    assert not prepared.rows
    assert prepared.issues[0].code == "INVALID_SNAPSHOT"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "invalid", ["TARGET", "SK_ID_CURR", "future", "too-many-records", "duplicate-reference"]
)
def test_invalid_raw_data_is_not_converted_to_incomplete(release, tmp_path, invalid):
    document = json.loads((FIXTURES / "catalog.json").read_text())
    raw = document["snapshots"]["demo-history"]
    if invalid in {"TARGET", "SK_ID_CURR"}:
        raw["application"][invalid] = 1
    elif invalid == "future":
        raw["history"]["bureau"]["records"][0]["DAYS_CREDIT"] = 1
    elif invalid == "too-many-records":
        raw["history"]["bureau"]["records"] *= 5001
    else:
        raw["history"]["bureau"]["records"] *= 2
    prepared = catalog(document).prepare(CSV, release, LocalStorage(tmp_path))
    assert not prepared.rows
    assert prepared.issues[0].code == "INVALID_SNAPSHOT"
    assert not list(tmp_path.iterdir())


def test_missing_and_explicit_empty_parent_sources(release, tmp_path):
    document = json.loads((FIXTURES / "catalog.json").read_text())
    raw = document["snapshots"]["demo-history"]
    raw["history"]["bureau"] = {"status": "not_provided", "records": []}
    raw["history"]["previous_applications"] = {"status": "provided", "records": []}
    raw["history"]["installments"] = {"status": "provided", "records": []}
    prepared = catalog(document).prepare(CSV, release, LocalStorage(tmp_path))
    assert not prepared.issues
    assert prepared.rows[0].missing_sources == ("bureau",)
    del raw["history"]["bureau"]
    omitted = catalog(document).prepare(CSV, release, LocalStorage(tmp_path))
    assert omitted.rows[0].missing_sources == ("bureau",)


def test_release_identity_pin_and_storage_corruption_fail_closed(release, tmp_path):
    store = LocalStorage(tmp_path)
    current = catalog()
    with pytest.raises(AppError) as error:
        stage_upload(
            CSV,
            catalog=current,
            release=release.model_copy(update={"feature_groups": []}),
            storage=store,
        )
    assert error.value.code == "MODEL_UNAVAILABLE"
    staged = stage_upload(CSV, catalog=current, release=release, storage=store)
    assert json.loads(staged.release_json)["model_sha256"] == "a" * 64
    prepared = current.prepare(CSV, release, store)
    key = prepared.rows[0].input_sha256
    path = tmp_path / key
    path.chmod(0o600)
    path.write_bytes(b"corrupted")
    with pytest.raises(StorageError):
        current.prepare(CSV, release, store)


def test_oversized_file_and_storage_failure_cannot_produce_a_staged_intent(
    release, tmp_path, monkeypatch
):
    store = LocalStorage(tmp_path)
    with pytest.raises(AppError) as error:
        stage_upload(b"x" * 1_048_577, catalog=catalog(), release=release, storage=store)
    assert error.value.status == 413
    assert not list(tmp_path.iterdir())

    def fail(_):
        raise StorageError("unavailable")

    monkeypatch.setattr(store, "put", fail)
    with pytest.raises(StorageError):
        stage_upload(CSV, catalog=catalog(), release=release, storage=store)
