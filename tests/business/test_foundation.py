"""Exercise PostgreSQL migrations, runtime privileges and atomic audit behavior."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from tests.business.conftest import migrate
from tests.conftest import REPO_ROOT

from risklab.business.audit import append_audit
from risklab.business.db import schema_ready, session_factory
from risklab.business.models import AuditEvent, User

pytestmark = pytest.mark.integration


def test_fresh_migration_upgrade_and_runtime_readiness(database):
    assert schema_ready(database.owner)
    assert schema_ready(database.runtime)
    assert {"users", "sessions", "audit_events", "alembic_version"} <= set(
        inspect(database.owner).get_table_names()
    )
    migrate(database.owner)  # Applying the current head twice is a no-op.
    assert schema_ready(database.runtime)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    with database.owner.begin() as connection:
        config.attributes["connection"] = connection
        command.check(config)


def test_audit_and_user_change_share_commit_or_rollback(database):
    factory = session_factory(database.runtime)
    user_id = uuid4()
    with factory.begin() as session:
        session.add(
            User(id=user_id, username="analyst.demo", password_hash="test-only", role="ANALYST")
        )
        session.flush()
        append_audit(
            session,
            action="user.created",
            resource_type="account",
            resource_id=user_id,
            actor_id=user_id,
            changes={"role": "ANALYST"},
        )
    with pytest.raises(RuntimeError), factory.begin() as session:
        user = session.get(User, user_id)
        user.role = "MANAGER"
        append_audit(
            session,
            action="user.changed",
            resource_type="account",
            resource_id=user_id,
            actor_id=user_id,
            changes={"role": "MANAGER"},
        )
        session.flush()
        raise RuntimeError("simulated transaction failure")
    with factory() as session:
        assert session.get(User, user_id).role == "ANALYST"
        assert len(session.scalars(select(AuditEvent)).all()) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'changed'",
        "DELETE FROM audit_events",
        "UPDATE alembic_version SET version_num = 'forged'",
        "CREATE TABLE forbidden (id integer)",
    ],
)
def test_runtime_cannot_edit_audit_schema_or_migration_identity(database, statement):
    with pytest.raises(DBAPIError), database.runtime.begin() as connection:
        connection.execute(text(statement))


@pytest.mark.parametrize(
    "field,value", [("role", "SUPERUSER"), ("revision", 0), ("username", "INVALID SPACE")]
)
def test_identity_constraints_enforced_by_postgres(database, field, value):
    values = {"username": "analyst.demo", "role": "ANALYST", "revision": 1}
    values[field] = value
    with pytest.raises(IntegrityError), session_factory(database.runtime).begin() as session:
        session.add(User(password_hash="test-only", **values))
        session.flush()


def test_readiness_rejects_missing_or_wrong_revision(database):
    assert not schema_ready(None)
    with database.owner.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'unknown'"))
    assert not schema_ready(database.runtime)


@pytest.mark.parametrize("database", ["0001_identity"], indirect=True)
def test_additive_upgrade_from_previous_revision_preserves_accounts(database):
    assert not schema_ready(database.runtime)
    user_id = uuid4()
    with session_factory(database.runtime).begin() as session:
        session.add(
            User(id=user_id, username="existing.account", role="ANALYST", password_hash="test-only")
        )
    migrate(database.owner)
    assert schema_ready(database.runtime)
    with session_factory(database.runtime)() as session:
        assert session.get(User, user_id).username == "existing.account"


@pytest.mark.parametrize("database", ["0004_workflow"], indirect=True)
def test_upgrade_grants_worker_only_operational_permissions(database):
    with database.worker.begin() as connection:
        assert connection.scalar(text("SELECT current_user")) == "risklab_worker"
        assert not connection.scalar(
            text("SELECT has_column_privilege(current_user, 'users', 'role', 'SELECT')")
        )
    migrate(database.owner)
    with database.worker.begin() as connection:
        assert connection.scalar(
            text("SELECT has_column_privilege(current_user, 'users', 'role', 'SELECT')")
        )
        assert connection.scalar(
            text(
                "SELECT has_column_privilege(current_user, "
                "'applications', 'scoring_state', 'UPDATE')"
            )
        )
        for table, column, permission in [
            ("users", "password_hash", "SELECT"),
            ("users", "role", "UPDATE"),
            ("applications", "assignee_id", "UPDATE"),
            ("reviews", "id", "INSERT"),
            ("decisions", "id", "INSERT"),
            ("sessions", "token_hash", "SELECT"),
        ]:
            assert not connection.scalar(
                text("SELECT has_column_privilege(current_user, :table, :column, :permission)"),
                {"table": table, "column": column, "permission": permission},
            )
