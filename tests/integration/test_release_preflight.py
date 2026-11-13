"""Render production Compose with the real CLI; no Docker workload or deployment starts."""

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime

import pytest
from scripts import backup_manifest

from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration


def run_preflight(extra_env, *args):
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {"RELEASE_MANIFEST", "RELEASE_DIR", "BACKUP_ROOT", "RISKLAB_ORIGIN", "SITE_ADDRESS"}
    }
    return subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/deploy.sh"), *args],
        cwd=REPO_ROOT.parent,
        env={**env, **extra_env},
        capture_output=True,
        text=True,
        timeout=45,
    )


@pytest.fixture
def compose_cli():
    if (
        not shutil.which("docker")
        or subprocess.run(
            ["docker", "compose", "version"], capture_output=True, check=False
        ).returncode
    ):
        pytest.skip("Real Docker Compose config verification requires the CLI")


def test_example_is_explicit_and_execute_is_unavailable(compose_cli):
    example = run_preflight({}, "--dry-run", "--example")
    assert example.returncode == 0, example.stdout + example.stderr
    assert "EXAMPLE ONLY" in example.stdout
    assert "NOT CHECKED" in example.stdout
    implicit = run_preflight({}, "--dry-run")
    assert implicit.returncode == 2 and "placeholders" in implicit.stdout
    forbidden = run_preflight({"RELEASE_MANIFEST": "/not-readable"}, "--execute")
    assert forbidden.returncode == 2 and "unavailable" in forbidden.stdout


def test_full_read_only_preflight_binds_bundle_and_fresh_backup(tmp_path, compose_cli):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "model.pkl").write_bytes(b"synthetic-opaque-bytes-not-deserialized")
    model_hash = hashlib.sha256((bundle / "model.pkl").read_bytes()).hexdigest()
    (bundle / "release.json").write_text(
        json.dumps(
            {
                "model_version": "synthetic-test-v1",
                "model_sha256": model_hash,
                "feature_contract_version": "1.1",
            }
        )
    )
    (bundle / "catalog.json").write_text(
        json.dumps({"catalog_version": "synthetic-b00-v1", "feature_contract_version": "1.1"})
    )
    (bundle / "comparison.json").write_text('{"aggregate_only": true}')
    manifest = json.loads((REPO_ROOT / "deploy/release-manifest.example.json").read_text())
    manifest.update(
        git_commit="a" * 40,
        api_image="registry.test/risklab/api@sha256:" + "b" * 64,
        web_image="registry.test/risklab/web@sha256:" + "c" * 64,
        model_version="synthetic-test-v1",
        model_sha256=model_hash,
    )
    for field in ("catalog", "release", "comparison"):
        manifest[field + "_sha256"] = hashlib.sha256(
            (bundle / (field + ".json")).read_bytes()
        ).hexdigest()
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest))
    backups = tmp_path / "backups"
    backup = backups / datetime.now(UTC).strftime("backup-%Y%m%dT%H%M%S.%fZ")
    backup.mkdir(parents=True)
    (backup / "objects").mkdir()
    (backup / "database.dump").write_bytes(b"synthetic-test-dump")
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "format": "risklab-backup/1",
                "backup_id": backup.name,
                "files": backup_manifest.inventory(backup),
            }
        )
    )
    env = {
        "RELEASE_MANIFEST": str(manifest_file),
        "RELEASE_DIR": str(bundle),
        "BACKUP_ROOT": str(backups),
        "RISKLAB_ORIGIN": "https://risklab.test",
        "SITE_ADDRESS": "risklab.test",
        "RISKLAB_TRUSTED_PROXY_IPS": "127.0.0.1",
        "RISKLAB_API_IMAGE": "incorrect-ambient-image",
        "COMPOSE_FILE": "missing-override.yml",
    }
    valid = run_preflight(env, "--dry-run")
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert "bundle bytes/identity" in valid.stdout and "backup integrity/freshness" in valid.stdout
    assert "NOT CHECKED" not in valid.stdout
    assert "unverified" in valid.stdout and "No SSH/pull/migration/traffic changes" in valid.stdout
    (bundle / "release.json").write_text('{"threshold":"corrupted"}')
    invalid = run_preflight(env, "--dry-run")
    assert invalid.returncode == 2 and "checksum mismatch for release.json" in invalid.stdout
    assert not invalid.stderr
