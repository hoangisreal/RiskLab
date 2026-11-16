"""Demo initializer guards: URL encoding and fail-closed bundle verification."""

from __future__ import annotations

import importlib.util

import pytest
from sqlalchemy.engine import make_url

from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.unit


def load_init():
    spec = importlib.util.spec_from_file_location(
        "demo_compose_init", REPO_ROOT / "scripts/demo_compose_init.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("password", ["Audit@Password:123", "a/b%c#d", "p@ss:w/rd%#", "simple"])
def test_database_url_roundtrips_special_passwords(password):
    url = load_init().database_url("risklab_api_login", password)
    parsed = make_url(url)
    assert parsed.password == password
    assert parsed.host == "postgres" and parsed.port == 5432
    assert parsed.username == "risklab_api_login"


def test_verify_bundle_fails_closed_without_model(tmp_path):
    (tmp_path / "release.json").write_text("{}")
    with pytest.raises(SystemExit, match="demo-reset"):
        load_init().verify_bundle(tmp_path)


def test_verify_bundle_fails_closed_on_corrupt_release(tmp_path):
    for name in ("model.pkl", "catalog.json", "comparison.json", "import.csv"):
        (tmp_path / name).write_bytes(b"synthetic")
    (tmp_path / "release.json").write_text("not-json")
    (tmp_path / "configs").mkdir()
    (tmp_path / "sql").mkdir()
    with pytest.raises(SystemExit, match="demo-reset"):
        load_init().verify_bundle(tmp_path)
