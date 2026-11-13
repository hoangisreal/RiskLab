"""Real PostgreSQL backup/restore using disposable client containers; no host installs."""

import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import text
from tests.conftest import REPO_ROOT

from risklab.business.db import session_factory
from risklab.business.identity import new_session
from risklab.business.models import User
from risklab.business.storage import LocalStorage

pytestmark = pytest.mark.integration


def test_backup_restore_refuses_nonempty_targets_and_verifies_inventory(database, tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker client needed for isolated PostgreSQL tools")
    tools_dir = tmp_path / "bin"
    tools_dir.mkdir()
    for tool in ("pg_dump", "pg_restore", "psql"):
        wrapper = tools_dir / tool
        wrapper.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'exec docker run --rm -i --user "$(id -u):$(id -g)" --network host '
            f'-v "{tmp_path}:{tmp_path}" '
            "--env PGHOST --env PGPORT --env PGDATABASE --env PGUSER --env PGPASSWORD "
            f'postgres:15-alpine {tool} "$@"\n'
        )
        wrapper.chmod(0o700)
    source_file = tmp_path / "source.url"
    source_file.write_text(
        database.owner.url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    source_file.chmod(0o600)
    storage = LocalStorage(tmp_path / "source-objects")
    key = storage.put(b"synthetic-immutable-snapshot")
    with session_factory(database.runtime).begin() as session:
        user = User(username="backup.synthetic", role="MANAGER", password_hash="synthetic-only")
        session.add(user)
        session.flush()
        new_session(session, user.id)
    backup_root = tmp_path / "backups"
    # Failed historical directories must never evict the verified backup.
    backup_root.mkdir()
    for index in range(9):
        (backup_root / f"backup-20990101T00000{index}.000000Z").mkdir()
    env = {
        **os.environ,
        "PATH": str(tools_dir) + os.pathsep + os.environ["PATH"],
        "BACKUP_ROOT": str(backup_root),
        "DATABASE_URL_FILE": str(source_file),
        "STORAGE_ROOT": str(storage.root),
        "OFFSITE_DIR": str(tmp_path / "offsite"),
        "ENCRYPTION_KEY_FILE": str(tmp_path / "backup.key"),
        # Client containers can access only this test's bind mount. Keep private
        # restore SQL there and never inherit an operator's real release folder.
        "TMPDIR": str(tmp_path),
        "RELEASE_DIR": "",
    }
    Path(env["ENCRYPTION_KEY_FILE"]).write_text("synthetic-encryption-secret-only")

    def run(script, extra=None):
        return subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / script)],
            env={**env, **(extra or {})},
            capture_output=True,
            text=True,
            timeout=120,
        )

    backed = run("backup.sh")
    assert backed.returncode == 0, backed.stderr
    copies = [p for p in backup_root.glob("backup-*") if (p / "manifest.json").exists()]
    assert len(copies) == 1
    backup = copies[0]
    assert list((tmp_path / "offsite").glob("*.enc"))
    assert not list(backup_root.glob(".pending-*"))
    unavailable_offsite = tmp_path / "offsite-is-a-file"
    unavailable_offsite.write_text("synthetic transport failure")
    failed_offsite = run("backup.sh", {"OFFSITE_DIR": str(unavailable_offsite)})
    assert failed_offsite.returncode != 0
    copies = [p for p in backup_root.glob("backup-*") if (p / "manifest.json").exists()]
    assert len(copies) == 2
    from scripts.backup_manifest import verify

    for copy in copies:
        verify(copy)
    target_storage = tmp_path / "restored-objects"
    common = {
        "BACKUP_DIR": str(backup),
        "TARGET_DATABASE_URL_FILE": str(source_file),
        "TARGET_STORAGE_ROOT": str(target_storage),
    }
    refused = run("restore.sh", common)
    assert refused.returncode != 0 and "nonempty" in refused.stderr
    with database.owner.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
    name = "risklab_test_restore_" + uuid4().hex
    control_url = database.owner.url.set(
        drivername="postgresql", database="postgres"
    ).render_as_string(hide_password=False)
    with psycopg.connect(control_url, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name))
        )
    target_file = tmp_path / "target.url"
    target_url = database.owner.url.set(drivername="postgresql", database=name).render_as_string(
        hide_password=False
    )
    target_file.write_text(target_url)
    try:
        common["TARGET_DATABASE_URL_FILE"] = str(target_file)
        restored = run("restore.sh", common)
        assert restored.returncode == 0, restored.stderr
        with psycopg.connect(target_url) as connection:
            assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert LocalStorage(target_storage).read(key) == b"synthetic-immutable-snapshot"
        # Tampering must be rejected before any restore command or object write.
        (backup / "database.dump").write_bytes(b"corrupt")
        invalid = run("restore.sh", {**common, "TARGET_STORAGE_ROOT": str(tmp_path / "other")})
        assert invalid.returncode != 0
        assert not (tmp_path / "other").exists()
    finally:
        with psycopg.connect(control_url, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )
