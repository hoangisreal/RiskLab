"""Configuration, HTTP boundary and immutable storage without external services."""

import hashlib
import logging
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from risklab.business.audit import append_audit
from risklab.business.main import create_app
from risklab.business.settings import Settings, SettingsError, load_settings
from risklab.business.storage import LocalStorage, StorageError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "env",
    [
        {"RISKLAB_DATABASE_URL": "secret-invalid-value"},
        {"RISKLAB_DATABASE_URL": "sqlite:///secret-location"},
        {"RISKLAB_DATABASE_URL": "", "RISKLAB_DATABASE_URL_FILE": "secret-path"},
        {"RISKLAB_DATABASE_URL_FILE": "/not-present/secret-file"},
        {"RISKLAB_DB_POOL_SIZE": "100"},
        {"RISKLAB_DB_POOL_TIMEOUT": "not-a-number"},
        {"RISKLAB_ENVIRONMENT": "production"},
        {"RISKLAB_ORIGIN": "https://site.example/attacker"},
        {"RISKLAB_ORIGIN": "http://localhost:invalid"},
    ],
)
def test_configuration_errors_do_not_echo_input(env):
    with pytest.raises(SettingsError) as error:
        load_settings(env)
    assert "secret" not in str(error.value)


def test_secret_file_and_settings_repr(tmp_path):
    url = "postgresql+psycopg://api:synthetic-secret@127.0.0.1/risklab"
    secret = tmp_path / "database-url"
    secret.write_text(url + "\n")
    config = load_settings({"RISKLAB_DATABASE_URL_FILE": str(secret)})
    assert config.database_url.get_secret_value() == url
    assert "synthetic-secret" not in repr(config)
    assert config.origin == "http://localhost:3000"


def test_health_is_independent_and_errors_are_safe(caplog):
    app = create_app(settings=Settings(environment="test"))

    @app.get("/api/v1/test-error")
    def fail():
        raise RuntimeError("sensitive-error-must-not-be-returned")

    @app.post("/api/v1/test-body")
    async def echo_length(request: Request):
        return {"data": len(await request.body())}

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        caplog.at_level(logging.INFO, logger="risklab.business"),
    ):
        live = client.get("/api/v1/health/live")
        assert live.status_code == 200
        assert live.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/health/ready").status_code == 503
        assert client.get("/predict").status_code == 404  # No legacy auth bypass mounted.
        error = client.get("/api/v1/test-error?value=sensitive-query")
        assert error.status_code == 500
        assert error.json()["error"]["request_id"] == error.headers["x-request-id"]
        assert "sensitive-error" not in error.text
        assert "sensitive-query" not in caplog.text
        assert "sensitive-error" not in caplog.text
        assert (
            client.post("/api/v1/test-body", content=iter([b"x" * 40000, b"x" * 40000])).status_code
            == 413
        )
        assert (
            client.post(
                "/api/v1/test-body", content=b"x", headers={"content-length": "-1"}
            ).status_code
            == 422
        )


def test_immutable_storage_deduplication_and_integrity(tmp_path):
    store = LocalStorage(tmp_path)
    key = store.put(b"synthetic-private-snapshot")
    assert key == hashlib.sha256(b"synthetic-private-snapshot").hexdigest()
    assert store.read(key) == b"synthetic-private-snapshot"
    inode = (tmp_path / key).stat().st_ino
    assert store.put(b"synthetic-private-snapshot") == key
    assert (tmp_path / key).stat().st_ino == inode
    (tmp_path / key).chmod(0o600)
    (tmp_path / key).write_bytes(b"corrupt")
    with pytest.raises(StorageError, match="integrity"):
        store.read(key)
    with pytest.raises(StorageError):
        store.put(b"synthetic-private-snapshot")
    assert not list(tmp_path.glob(".pending-*"))


@pytest.mark.parametrize(
    "key", ["../private", "/etc/passwd", "https://example.com", "a" * 63, "A" * 64]
)
def test_storage_rejects_paths_and_noncanonical_keys(tmp_path, key):
    with pytest.raises(StorageError, match="Invalid object key"):
        LocalStorage(tmp_path).read(key)


def test_storage_rejects_symlinks_and_cleans_interrupted_writes(tmp_path, monkeypatch):
    store = LocalStorage(tmp_path)
    external = tmp_path / "external"
    external.write_bytes(b"sensitive")
    (tmp_path / ("a" * 64)).symlink_to(external)
    with pytest.raises(StorageError):
        store.read("a" * 64)
    monkeypatch.setattr("os.link", lambda *args: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        store.put(b"new-object")
    assert not list(tmp_path.glob(".pending-*"))
    assert external.read_bytes() == b"sensitive"


def test_audit_rejects_arbitrary_sensitive_metadata():
    with pytest.raises(ValueError, match="allowlisted"):
        append_audit(
            None,
            action="test",
            resource_type="account",
            resource_id=uuid4(),
            changes={"password": "not-allowed"},
        )
