"""Disposable PostgreSQL databases, real migrations and a non-owner runtime login.

No developer/production database URL is consumed implicitly. Without Docker, CI
can provide RISKLAB_TEST_DATABASE_URL naming an explicit risklab_test_control DB.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from tests.conftest import REPO_ROOT


def migrate(engine: Engine, revision: str = "head") -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


@contextmanager
def disposable_postgres() -> Iterator[tuple[URL, str]]:
    configured = os.environ.get("RISKLAB_TEST_DATABASE_URL")
    container: str | None = None
    with tempfile.TemporaryDirectory(prefix="risklab-pg-", dir="/tmp/opencode") as temporary:
        if configured:
            url = make_url(configured)
            if url.database != "risklab_test_control" or url.drivername != "postgresql+psycopg":
                pytest.fail("Test URL must explicitly name PostgreSQL risklab_test_control")
        else:
            if not shutil.which("docker"):
                pytest.skip("PostgreSQL tests need Docker or explicit test control database")
            available = subprocess.run(["docker", "info"], capture_output=True)
            if available.returncode:
                pytest.skip("Docker is unavailable; PostgreSQL behavior has not been verified")
            container = "risklab-test-" + uuid4().hex
            password = secrets.token_urlsafe(24)
            env_file = Path(temporary) / "database.env"
            env_file.write_text(f"POSTGRES_PASSWORD={password}\n")
            env_file.chmod(0o600)
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    container,
                    "--label",
                    "risklab.e2e=" + os.environ.get("RISKLAB_E2E_RUN_ID", "pytest"),
                    "--env-file",
                    str(env_file),
                    "-p",
                    "127.0.0.1::5432",
                    "postgres:15-alpine",
                ],
                capture_output=True,
                check=True,
            )
            port = subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
                    container,
                ],
                text=True,
            ).strip()
            url = URL.create(
                "postgresql+psycopg",
                username="postgres",
                password=password,
                host="127.0.0.1",
                port=int(port),
                database="postgres",
            )
        runtime_password = secrets.token_urlsafe(24)
        login = "risklab_test_" + uuid4().hex
        try:
            deadline = time.monotonic() + 40
            while True:
                try:
                    with psycopg.connect(
                        url.set(drivername="postgresql").render_as_string(hide_password=False),
                        autocommit=True,
                    ) as connection:
                        connection.execute((REPO_ROOT / "deploy/postgres-roles.sql").read_text())
                        connection.execute(
                            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                                sql.Identifier(login), sql.Literal(runtime_password)
                            )
                        )
                        connection.execute(
                            sql.SQL("GRANT risklab_runtime TO {}").format(sql.Identifier(login))
                        )
                    break
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        pytest.fail("Disposable PostgreSQL did not become ready")
                    time.sleep(0.2)
            yield (
                url,
                URL.create(
                    "postgresql+psycopg",
                    username=login,
                    password=runtime_password,
                    host=url.host,
                    port=url.port,
                ).render_as_string(hide_password=False),
            )
        finally:
            if container:
                subprocess.run(["docker", "rm", "--force", container], capture_output=True)
            elif configured:
                with psycopg.connect(
                    url.set(drivername="postgresql").render_as_string(hide_password=False),
                    autocommit=True,
                ) as connection:
                    connection.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(login))
                    )


@dataclass
class Database:
    owner: Engine
    runtime: Engine


@contextmanager
def disposable_database(
    postgres_control: tuple[URL, str], revision: str = "head"
) -> Iterator[Database]:
    control, runtime = postgres_control
    name = "risklab_test_" + uuid4().hex
    connection_string = control.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection_string, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    owner = create_engine(control.set(database=name), hide_parameters=True)
    application = create_engine(make_url(runtime).set(database=name), hide_parameters=True)
    try:
        with owner.begin() as connection:
            connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        migrate(owner, revision)
        yield Database(owner, application)
    finally:
        application.dispose()
        owner.dispose()
        with psycopg.connect(connection_string, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture(scope="session")
def postgres_control() -> Iterator[tuple[URL, str]]:
    with disposable_postgres() as control:
        yield control


@pytest.fixture
def database(
    postgres_control: tuple[URL, str], request: pytest.FixtureRequest
) -> Iterator[Database]:
    with disposable_database(postgres_control, getattr(request, "param", "head")) as current:
        yield current
