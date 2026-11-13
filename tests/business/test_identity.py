"""Real HTTP + PostgreSQL authorization, revocation, CSRF and concurrent account changes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from risklab.business.db import session_factory
from risklab.business.errors import AppError
from risklab.business.identity import HASHER, digest, ensure_application_access
from risklab.business.main import create_app
from risklab.business.models import AuditEvent, LoginSession, User, now_utc
from risklab.business.settings import Settings

pytestmark = pytest.mark.integration
PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def identity_env(database):
    factory = session_factory(database.runtime)
    with factory.begin() as session:
        for name, role in [
            ("admin.demo", "ADMIN"),
            ("second.admin", "ADMIN"),
            ("manager.demo", "MANAGER"),
            ("analyst.demo", "ANALYST"),
        ]:
            session.add(User(username=name, role=role, password_hash=HASHER.hash(PASSWORD)))
    app = create_app(
        settings=Settings(environment="test", origin="http://testserver"), engine=database.runtime
    )
    return app, factory


def signed_in(app, username="admin.demo"):
    client = TestClient(app)
    client.headers["origin"] = "http://testserver"
    csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"]
    client.headers["x-csrf-token"] = csrf
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
    return client


def test_login_rotates_session_only_stores_digests_and_logout_revokes(identity_env):
    app, factory = identity_env
    client = TestClient(app)
    csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"]
    before = client.cookies["risklab_dev_session"]
    client.headers.update({"origin": "http://testserver", "x-csrf-token": csrf})
    result = client.post(
        "/api/v1/auth/login", json={"username": "admin.demo", "password": PASSWORD}
    )
    assert result.status_code == 200
    assert "password" not in result.text
    after = client.cookies["risklab_dev_session"]
    assert before != after
    csrf = result.json()["data"]["csrf_token"]
    with factory() as session:
        assert session.get(LoginSession, digest(before)) is None
        record = session.get(LoginSession, digest(after))
        assert record.csrf_hash == digest(csrf)
        assert record.token_hash != after
        assert session.scalar(select(User)).password_hash.startswith("$argon2id$")
    client.headers["x-csrf-token"] = csrf
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    with factory() as session:
        assert session.get(LoginSession, digest(after)) is None


@pytest.mark.parametrize("failure", ["no-token", "hostile-origin", "expired", "wrong-token"])
def test_csrf_mutations_fail_closed(identity_env, failure):
    app, factory = identity_env
    client = signed_in(app)
    if failure == "no-token":
        del client.headers["x-csrf-token"]
    elif failure == "hostile-origin":
        client.headers["origin"] = "http://attacker.invalid"
    elif failure == "wrong-token":
        client.headers["x-csrf-token"] = "x" * 43
    else:
        with factory.begin() as session:
            record = session.get(LoginSession, digest(client.cookies["risklab_dev_session"]))
            record.expires_at = now_utc() - timedelta(seconds=1)
    assert client.post(
        "/api/v1/admin/users",
        json={"username": "new.analyst", "password": PASSWORD, "role": "ANALYST"},
    ).status_code in {401, 403}
    with factory() as session:
        assert session.scalar(select(User).where(User.username == "new.analyst")) is None


@pytest.mark.parametrize("username", ["manager.demo", "analyst.demo"])
def test_account_admin_is_not_inherited_by_business_roles(identity_env, username):
    app, _ = identity_env
    client = signed_in(app, username)
    assert client.get("/api/v1/admin/users").status_code == 403
    assert (
        client.post(
            "/api/v1/admin/users",
            json={"username": "blocked.user", "password": PASSWORD, "role": "ADMIN"},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/admin/audit-events").status_code == 403
    assert client.get("/api/v1/analysts").status_code == (
        200 if username == "manager.demo" else 403
    )


def test_admin_create_patch_reset_and_session_revocation(identity_env):
    app, factory = identity_env
    admin = signed_in(app)
    created = admin.post(
        "/api/v1/admin/users",
        json={"username": "new.analyst", "password": PASSWORD, "role": "ANALYST"},
    )
    assert created.status_code == 201, created.text
    duplicate = admin.post(
        "/api/v1/admin/users",
        json={"username": "new.analyst", "password": PASSWORD, "role": "ANALYST"},
    )
    assert duplicate.status_code == 409
    target = created.json()["data"]
    analyst = signed_in(app, "new.analyst")
    changed = admin.patch(
        f"/api/v1/admin/users/{target['id']}",
        json={"expected_revision": 1, "reason": "Demo role change", "role": "MANAGER"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["revision"] == 2
    assert analyst.get("/api/v1/auth/me").status_code == 401
    assert (
        admin.patch(
            f"/api/v1/admin/users/{target['id']}",
            json={"expected_revision": 1, "reason": "Stale", "is_active": False},
        ).status_code
        == 409
    )
    manager = signed_in(app, "new.analyst")
    reset = admin.post(
        f"/api/v1/admin/users/{target['id']}/reset-password",
        json={
            "expected_revision": 2,
            "reason": "Reset test",
            "new_password": "another-synthetic-password",
        },
    )
    assert reset.status_code == 204
    assert manager.get("/api/v1/auth/me").status_code == 401
    with factory() as session:
        user = session.get(User, UUID(target["id"]))
        assert user.revision == 3
        assert HASHER.verify(user.password_hash, "another-synthetic-password")
    audit = admin.get(f"/api/v1/admin/audit-events?user_id={target['id']}").json()
    assert audit["pagination"]["total"] >= 3
    assert PASSWORD not in str(audit) and "another-synthetic-password" not in str(audit)


def test_disable_revokes_sessions_and_preserves_actor_identity(identity_env):
    app, factory = identity_env
    admin = signed_in(app)
    analyst = signed_in(app, "analyst.demo")
    target = analyst.get("/api/v1/auth/me").json()["data"]
    result = admin.patch(
        f"/api/v1/admin/users/{target['id']}",
        json={"expected_revision": 1, "reason": "Disable test", "is_active": False},
    )
    assert result.status_code == 200
    assert analyst.get("/api/v1/auth/me").status_code == 401
    with factory() as session:
        assert session.get(User, UUID(target["id"])) is not None
        assert session.scalars(
            select(AuditEvent).where(AuditEvent.actor_id == UUID(target["id"]))
        ).all()


def test_self_role_change_and_last_admin_concurrent_changes(identity_env):
    app, factory = identity_env
    first, second = signed_in(app), signed_in(app, "second.admin")
    first_id = first.get("/api/v1/auth/me").json()["data"]["id"]
    second_id = second.get("/api/v1/auth/me").json()["data"]["id"]
    assert (
        first.patch(
            f"/api/v1/admin/users/{first_id}",
            json={"expected_revision": 1, "role": "MANAGER", "reason": "Forbidden self change"},
        ).status_code
        == 403
    )

    def disable(pair):
        client, user_id = pair
        return client.patch(
            f"/api/v1/admin/users/{user_id}",
            json={"expected_revision": 1, "is_active": False, "reason": "Concurrent test"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(disable, [(first, first_id), (second, second_id)]))
    assert sorted(results) == [200, 409]
    with factory() as session:
        assert (
            len(
                session.scalars(
                    select(User).where(User.role == "ADMIN", User.is_active.is_(True))
                ).all()
            )
            == 1
        )


def test_rate_limit_attempts_commit_on_failure_and_do_not_echo_input(identity_env, caplog):
    app, _ = identity_env
    client = TestClient(app)
    client.headers.update(
        {
            "origin": "http://testserver",
            "x-csrf-token": client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"],
        }
    )
    for _ in range(10):
        response = client.post(
            "/api/v1/auth/login", json={"username": "not.present", "password": PASSWORD}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    blocked = client.post(
        "/api/v1/auth/login", json={"username": "admin.demo", "password": PASSWORD}
    )
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "300"
    assert PASSWORD not in caplog.text


def test_query_pagination_and_body_mass_assignment_are_rejected(identity_env):
    app, _ = identity_env
    client = signed_in(app)
    assert client.get("/api/v1/admin/users?page_size=1000").status_code == 422
    assert client.get("/api/v1/admin/users?role=SUPERUSER").status_code == 422
    assert client.get("/api/v1/admin/users?raw_sql=1").status_code == 422
    assert client.get("/api/v1/auth/me?actor_id=forged").status_code == 422
    assert client.get("/api/v1/admin/audit-events?raw_sql=1").status_code == 422
    own_id = client.get("/api/v1/auth/me").json()["data"]["id"]
    assert (
        client.patch(
            f"/api/v1/admin/users/{own_id}",
            json={
                "expected_revision": 1,
                "reason": "Invalid null",
                "role": None,
                "is_active": True,
            },
        ).status_code
        == 422
    )
    assert client.get("/api/v1/admin/users?page=100").json()["data"] == []
    assert (
        client.post(
            "/api/v1/admin/users",
            json={
                "username": "forged.actor",
                "password": PASSWORD,
                "role": "ANALYST",
                "actor_id": str(uuid4()),
            },
        ).status_code
        == 422
    )


def test_object_access_contract_and_admin_separation():
    owner = uuid4()
    analyst = User(id=owner, username="analyst.demo", password_hash="test", role="ANALYST")
    ensure_application_access(analyst, owner)
    with pytest.raises(AppError) as error:
        ensure_application_access(analyst, uuid4())
    assert error.value.status == 404
    ensure_application_access(User(role="MANAGER"), None)
    with pytest.raises(AppError) as error:
        ensure_application_access(User(role="ADMIN"), owner)
    assert error.value.status == 403


def test_production_session_cookie_flags(database):
    app = create_app(
        settings=Settings(environment="production", origin="https://risklab.example"),
        engine=database.runtime,
    )
    with TestClient(app, base_url="https://risklab.example") as client:
        response = client.get("/api/v1/auth/csrf")
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Host-risklab_session=")
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        assert "Domain=" not in cookie


def test_bootstrap_admin_only_on_empty_database(database, monkeypatch):
    from pydantic import SecretStr
    from typer.testing import CliRunner

    import risklab.business.cli as cli

    settings = Settings(
        environment="test",
        database_url=SecretStr(database.runtime.url.render_as_string(hide_password=False)),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: PASSWORD)
    runner = CliRunner()
    created = runner.invoke(cli.app, ["bootstrap-admin", "initial.admin"])
    assert created.exit_code == 0, created.output
    assert PASSWORD not in created.output
    again = runner.invoke(cli.app, ["bootstrap-admin", "another.admin"])
    assert again.exit_code != 0
    with session_factory(database.runtime)() as session:
        assert len(session.scalars(select(User)).all()) == 1
        assert session.scalar(select(AuditEvent)).actor_type == "SYSTEM"


def test_session_is_rechecked_after_account_lock_wait(identity_env, monkeypatch):
    """Model a reset that commits after the token read and before the user lock."""
    from threading import Event

    from sqlalchemy import delete, event

    app, factory = identity_env
    client = signed_in(app)
    token = digest(client.cookies["risklab_dev_session"])
    reached = Event()
    resume = Event()
    engine = app.state.engine

    def pause(connection, cursor, statement, parameters, context, executemany):
        if "FROM users" in statement and "FOR SHARE" in statement:
            reached.set()
            assert resume.wait(5)

    event.listen(engine, "before_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(
                client.post,
                "/api/v1/admin/users",
                json={
                    "username": "revoked.attempt",
                    "password": PASSWORD,
                    "role": "ANALYST",
                },
            )
            assert reached.wait(5)
            with factory.begin() as session:
                session.execute(delete(LoginSession).where(LoginSession.token_hash == token))
            resume.set()
            assert result.result(timeout=5).status_code in {401, 403}
    finally:
        resume.set()
        event.remove(engine, "before_cursor_execute", pause)
    with factory() as session:
        assert session.scalar(select(User).where(User.username == "revoked.attempt")) is None


@pytest.mark.parametrize("trusted", [True, False])
def test_forwarded_client_ip_rate_limit_respects_proxy_boundary(identity_env, trusted):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app, _ = identity_env
    peer = "192.0.2.10" if trusted else "192.0.2.99"
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=["192.0.2.10"])
    client = TestClient(wrapped, client=(peer, 1234))
    client.headers.update({"origin": "http://testserver", "x-forwarded-for": "198.51.100.1"})
    csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"]
    client.headers["x-csrf-token"] = csrf
    for _ in range(10):
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "missing.user", "password": PASSWORD}
            ).status_code
            == 401
        )
    client.headers["x-forwarded-for"] = "198.51.100.2"
    response = client.post(
        "/api/v1/auth/login", json={"username": "manager.demo", "password": PASSWORD}
    )
    assert response.status_code == (200 if trusted else 429)
