"""Real PostgreSQL recovery faults must not publish a half-restored identity/session state."""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import create_engine, func, select, text
from tests.business.conftest import Database
from tests.conftest import REPO_ROOT

from risklab.business.db import session_factory
from risklab.business.identity import new_session
from risklab.business.jobs import claim_job, complete_job, enqueue_job
from risklab.business.models import ImportBatch, Job, LoginSession, User, now_utc
from risklab.business.storage import LocalStorage

pytestmark = pytest.mark.integration
PRIVATE_ERROR = "synthetic-private-error-must-not-appear"


@dataclass
class RecoveryCase:
    database: Database
    root: Path
    env: dict[str, str]
    key: str
    live_job: UUID
    exhausted_job: UUID

    def run(self, script: str, extra: dict[str, str] | None = None):
        return subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / script)],
            env={**self.env, **(extra or {})},
            capture_output=True,
            text=True,
            timeout=120,
        )

    def backup(self) -> Path:
        result = self.run("backup.sh")
        assert result.returncode == 0, result.stderr
        copies = list(Path(self.env["BACKUP_ROOT"]).glob("backup-*"))
        assert len(copies) == 1
        return copies[0]

    @contextmanager
    def target(self):
        name = "risklab_test_recovery_" + uuid4().hex
        control_url = self.database.owner.url.set(
            drivername="postgresql", database="postgres"
        ).render_as_string(hide_password=False)
        with psycopg.connect(control_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name))
            )
        url_file = self.root / (name + ".url")
        url_file.write_text(
            self.database.owner.url.set(drivername="postgresql", database=name).render_as_string(
                hide_password=False
            )
        )
        url_file.chmod(0o600)
        engine = create_engine(self.database.owner.url.set(database=name), hide_parameters=True)
        try:
            yield (
                engine,
                {
                    "TARGET_DATABASE_URL_FILE": str(url_file),
                    "TARGET_STORAGE_ROOT": str(self.root / (name + "-objects")),
                },
            )
        finally:
            engine.dispose()
            with psycopg.connect(control_url, autocommit=True) as connection:
                connection.execute(
                    psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        psycopg.sql.Identifier(name)
                    )
                )


@pytest.fixture
def recovery_case(database, tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker client tools required for isolated PostgreSQL rehearsal")
    tools = tmp_path / "bin"
    tools.mkdir()
    for tool in ("pg_dump", "pg_restore", "psql"):
        wrapper = tools / tool
        wrapper.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'exec docker run --rm -i --user "$(id -u):$(id -g)" --network host '
            f'-v "{tmp_path}:{tmp_path}" '
            "--env PGHOST --env PGPORT --env PGDATABASE --env PGUSER --env PGPASSWORD "
            f'postgres:15-alpine {tool} "$@"\n'
        )
        wrapper.chmod(0o700)
    source = tmp_path / "source.url"
    source.write_text(
        database.owner.url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    source.chmod(0o600)
    storage = LocalStorage(tmp_path / "source-objects")
    key = storage.put(b"synthetic-snapshot")
    jobs = []
    with session_factory(database.runtime).begin() as session:
        user = User(username="recovery.manager", role="MANAGER", password_hash="synthetic-only")
        session.add(user)
        session.flush()
        new_session(session, user.id)
        for attempt in (1, 3):
            batch = ImportBatch(
                actor_id=user.id,
                catalog_version="synthetic-test",
                catalog_sha256="a" * 64,
                file_sha256=key,
                import_contract_version="demo-import/1",
                release_identity={},
                state="RUNNING",
            )
            session.add(batch)
            session.flush()
            job = enqueue_job(session, kind="IMPORT", import_id=batch.id, initiated_by=user.id)
            job.state = "RUNNING"
            job.attempt = attempt
            job.lease_owner = "pre-restore-worker"
            job.lease_expires_at = now_utc() + timedelta(hours=1)
            jobs.append(job.id)
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "BACKUP_ROOT": str(tmp_path / "backups"),
        "DATABASE_URL_FILE": str(source),
        "STORAGE_ROOT": str(storage.root),
        "RELEASE_DIR": "",
        "OFFSITE_DIR": "",
        "ENCRYPTION_KEY_FILE": "",
        "TMPDIR": str(tmp_path),
    }
    return RecoveryCase(database, tmp_path, env, key, *jobs)


@pytest.mark.parametrize("failing_table", ["sessions", "jobs"])
def test_recovery_failure_rolls_back_restored_schema_data_and_sessions(
    recovery_case, failing_table
):
    case = recovery_case
    operation = "DELETE" if failing_table == "sessions" else "UPDATE"
    with case.database.owner.begin() as connection:
        # The fault fires after pg_restore's post-data phase, not during import of rows.
        connection.execute(
            text(f"""
            CREATE FUNCTION fail_recovery() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION '{PRIVATE_ERROR}'; END $$;
            CREATE TRIGGER fail_recovery BEFORE {operation} ON {failing_table}
            FOR EACH ROW EXECUTE FUNCTION fail_recovery();
        """)
        )
    backup = case.backup()
    with case.target() as (target, env):
        failed = case.run("restore.sh", {"BACKUP_DIR": str(backup), **env})
        assert failed.returncode != 0
        with target.connect() as connection:
            count = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public'"
                )
            )
            assert count == 0, "Recovery failed after publishing restored accounts/sessions/schema"
        assert PRIVATE_ERROR not in failed.stdout + failed.stderr
        assert "restore completed" not in failed.stdout
    assert not list(case.root.glob("risklab-restore-*"))


def test_success_revokes_sessions_and_fences_old_jobs_without_resetting_retry_budget(recovery_case):
    case = recovery_case
    backup = case.backup()
    with case.target() as (target, env):
        restored = case.run("restore.sh", {"BACKUP_DIR": str(backup), **env})
        assert restored.returncode == 0, restored.stderr
        factory = session_factory(target)
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(LoginSession)) == 0
            assert session.scalar(select(func.count()).select_from(User)) == 1
            for job_id, attempt in ((case.live_job, 1), (case.exhausted_job, 3)):
                job = session.get(Job, job_id)
                assert job.state == "QUEUED" and job.attempt == attempt
                assert job.lease_owner is None and job.lease_expires_at is None
        worker = create_engine(
            target.url, hide_parameters=True, connect_args={"options": "-c role=risklab_worker"}
        )
        try:
            with session_factory(worker).begin() as session:
                for job_id, attempt in ((case.live_job, 1), (case.exhausted_job, 3)):
                    assert not complete_job(
                        session, job_id=job_id, attempt=attempt, worker_id="pre-restore-worker"
                    )
                resumed = claim_job(session, worker_id="restored-worker")
                assert resumed.id == case.live_job and resumed.attempt == 2
                assert claim_job(session, worker_id="restored-worker") is None
                exhausted = session.get(Job, case.exhausted_job)
                assert exhausted.state == "FAILED" and exhausted.attempt == 3
        finally:
            worker.dispose()
        assert (
            LocalStorage(Path(env["TARGET_STORAGE_ROOT"])).read(case.key) == b"synthetic-snapshot"
        )
    assert not list(case.root.glob("risklab-restore-*"))


def test_archive_conversion_failure_does_not_write_target_objects_or_database(recovery_case):
    case = recovery_case
    backup = case.backup()
    wrapper = Path(case.env["PATH"].split(os.pathsep)[0]) / "pg_restore"
    wrapper.write_text(f"#!/bin/sh\necho '{PRIVATE_ERROR}' >&2\nexit 9\n")
    with case.target() as (target, env):
        failed = case.run("restore.sh", {"BACKUP_DIR": str(backup), **env})
        assert failed.returncode != 0
        assert not Path(env["TARGET_STORAGE_ROOT"]).exists()
        with target.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace")
                )
                == 0
            )
        assert PRIVATE_ERROR not in failed.stdout + failed.stderr
    assert not list(case.root.glob("risklab-restore-*"))
