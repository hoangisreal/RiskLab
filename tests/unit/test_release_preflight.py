"""Release preflight must inspect the intended bytes/config, never imply deployment."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from scripts import backup_manifest
from scripts import release_preflight as preflight

pytestmark = pytest.mark.unit


@pytest.fixture
def manifest_data():
    return {
        "format": "risklab-release/2",
        "git_commit": "a" * 40,
        "api_image": "registry.test/risklab/api@sha256:" + "b" * 64,
        "web_image": "registry.test/risklab/web@sha256:" + "c" * 64,
        "schema_revision": "0005_worker_permissions",
        "model_version": "synthetic-test-v1",
        "model_sha256": "d" * 64,
        "catalog_version": "synthetic-b00-v1",
        "catalog_sha256": "e" * 64,
        "release_sha256": "f" * 64,
        "comparison_sha256": "1" * 64,
    }


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "risklab-release/1"),
        ("git_commit", "a" * 39),
        ("git_commit", "0" * 40),
        ("api_image", "registry.test/api:latest"),
        ("web_image", "-not-a-reference@sha256:" + "a" * 64),
        ("api_image", "registry.test/user:password@host/api@sha256:" + "a" * 64),
        ("catalog_sha256", "not-a-hash"),
        ("release_sha256", "0" * 64),
        ("schema_revision", "../../unsafe"),
        ("catalog_version", "sensitive value"),
        ("model_version", "operator-pinned-model-version"),
        ("model_sha256", True),
    ],
)
def test_manifest_rejects_invalid_or_placeholder_fields(tmp_path, manifest_data, field, value):
    manifest_data[field] = value
    with pytest.raises(preflight.PreflightError):
        preflight.manifest(write_json(tmp_path / "release.json", manifest_data))


def test_manifest_requires_exact_fields_and_template_requires_explicit_mode(
    tmp_path, manifest_data
):
    path = write_json(tmp_path / "release.json", manifest_data)
    assert preflight.manifest(path) == manifest_data
    for removed in ("schema_revision", "release_sha256", "comparison_sha256"):
        value = {k: v for k, v in manifest_data.items() if k != removed}
        with pytest.raises(preflight.PreflightError):
            preflight.manifest(write_json(path, value))
    with pytest.raises(preflight.PreflightError):
        preflight.manifest(write_json(path, {**manifest_data, "unknown": "sensitive-value"}))
    example = preflight.ROOT / "deploy/release-manifest.example.json"
    with pytest.raises(preflight.PreflightError, match="placeholders"):
        preflight.manifest(example)
    assert preflight.manifest(example, example=True)["format"] == "risklab-release/2"


def test_company_name_containing_example_is_not_a_template(tmp_path, manifest_data):
    manifest_data["api_image"] = "registry.myexamplecompany.test/team/api@sha256:" + "b" * 64
    assert (
        preflight.manifest(write_json(tmp_path / "manifest.json", manifest_data)) == manifest_data
    )


@pytest.mark.parametrize(
    "content", [b"[]", b"{", b"\xff", b'{"a":1,"a":2}', b'{"value":NaN}', b'{"value":Infinity}']
)
def test_json_parser_rejects_ambiguous_or_non_json_metadata(tmp_path, content):
    path = tmp_path / "data.json"
    path.write_bytes(content)
    with pytest.raises(preflight.PreflightError):
        preflight.read_json(path)


def test_json_parser_bounds_bytes_and_rejects_symlink_inputs(tmp_path):
    real = write_json(tmp_path / "data.json", {"safe": True})
    with pytest.raises(preflight.PreflightError, match="size limit"):
        preflight.read_json(real, max_bytes=1)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(preflight.PreflightError, match="regular file"):
        preflight.read_json(link)
    directory = tmp_path / "indirect"
    directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(preflight.PreflightError, match="regular file"):
        preflight.read_json(directory / "data.json")


def rendered(value):
    services = {
        name: {
            "image": value["api_image"],
            "environment": {
                "RISKLAB_CATALOG_SHA256": value["catalog_sha256"],
                "FORWARDED_ALLOW_IPS": "127.0.0.1",
                "RISKLAB_ORIGIN": "https://risklab.example",
            },
        }
        for name in ("api", "worker", "migrate")
    }
    services["migrate"]["command"] = ["/app/.venv/bin/alembic", "upgrade", "head"]
    services["worker"]["command"] = ["/app/.venv/bin/risklab-worker"]
    services["web"] = {"image": value["web_image"]}
    services["caddy"] = {
        "ports": [{"published": "80", "target": 80}, {"published": "443", "target": 443}],
        "environment": {"SITE_ADDRESS": "risklab.example"},
    }
    return {"services": services}


def test_compose_uses_manifest_values_ignores_dotenv_and_never_runs_a_deploy(
    monkeypatch, manifest_data
):
    calls = []

    def run(argv, **options):
        calls.append(argv)
        assert argv[-3:] == ["config", "--format", "json"]
        assert options["env"]["RISKLAB_API_IMAGE"] == manifest_data["api_image"]
        assert options["env"]["RISKLAB_WEB_IMAGE"] == manifest_data["web_image"]
        assert options["env"]["RISKLAB_CATALOG_SHA256"] == manifest_data["catalog_sha256"]
        assert "COMPOSE_FILE" not in options["env"]
        assert "COMPOSE_ENV_FILES" not in options["env"]
        assert argv[argv.index("--env-file") + 1] == "/dev/null"
        result = rendered(manifest_data)
        for name in ("api", "worker"):
            result["services"][name]["environment"]["RISKLAB_ORIGIN"] = "https://risklab.test"
        result["services"]["caddy"]["environment"]["SITE_ADDRESS"] = "risklab.test"
        return SimpleNamespace(returncode=0, stdout=json.dumps(result))

    monkeypatch.setattr(preflight.subprocess, "run", run)
    preflight.compose_config(
        manifest_data,
        {
            "RISKLAB_ORIGIN": "https://risklab.test",
            "SITE_ADDRESS": "risklab.test",
            "RISKLAB_TRUSTED_PROXY_IPS": "127.0.0.1",
            "RISKLAB_API_IMAGE": "ambient-wrong-image",
            "COMPOSE_FILE": "untrusted.yml",
            "COMPOSE_ENV_FILES": "private.env",
        },
        example=False,
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "image",
        "catalog",
        "private-port",
        "worker-command",
        "migration-command",
        "ingress",
        "host-network",
        "proxy-trust",
        "api-origin",
        "worker-origin",
        "site-address",
    ],
)
def test_rendered_compose_mismatch_is_failure(monkeypatch, manifest_data, mutation):
    result = rendered(manifest_data)
    services = result["services"]
    if mutation == "image":
        services["api"]["image"] = "old-image"
    elif mutation == "catalog":
        services["worker"]["environment"]["RISKLAB_CATALOG_SHA256"] = "old-catalog"
    elif mutation == "private-port":
        services["postgres"] = {"ports": [{"published": "5432"}]}
    elif mutation == "worker-command":
        services["worker"]["command"] = ["wrong"]
    elif mutation == "ingress":
        services["caddy"]["ports"].append({"published": "5432", "target": 5432})
    elif mutation in {"api-origin", "worker-origin"}:
        services[mutation.split("-")[0]]["environment"]["RISKLAB_ORIGIN"] = "https://wrong.test"
    elif mutation == "site-address":
        services["caddy"]["environment"]["SITE_ADDRESS"] = "wrong.test"
    elif mutation == "proxy-trust":
        services["api"]["environment"]["FORWARDED_ALLOW_IPS"] = "*"
    elif mutation == "host-network":
        services["api"]["network_mode"] = "host"
    else:
        services["migrate"]["command"] = ["true"]
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(result)),
    )
    with pytest.raises(preflight.PreflightError, match="Rendered Compose"):
        preflight.compose_config(manifest_data, {}, example=True)


@pytest.mark.parametrize("failure", ["error", "missing", "timeout"])
def test_compose_failure_is_not_silently_skipped_or_echoed(monkeypatch, manifest_data, failure):
    def run(*args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("secret executable path")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("secret command", 30)
        return SimpleNamespace(returncode=1, stderr="sensitive interpolated value", stdout="")

    monkeypatch.setattr(preflight.subprocess, "run", run)
    with pytest.raises(preflight.PreflightError) as error:
        preflight.compose_config(manifest_data, {}, example=True)
    assert "secret" not in str(error.value) and "sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "origin,site",
    [
        ("http://risklab.test", "risklab.test"),
        ("https://user:secret@risklab.test", "risklab.test"),
        ("https://risklab.test/path", "risklab.test"),
        ("https://risklab.test", "other.test"),
        ("https://risklab.test:wrong", "risklab.test"),
        ("https://risklab.test/", "risklab.test"),
    ],
)
def test_public_origin_must_match_the_site_without_credentials(origin, site):
    with pytest.raises(preflight.PreflightError):
        preflight.deployment_origin({"RISKLAB_ORIGIN": origin, "SITE_ADDRESS": site}, example=False)


def make_bundle(root, value):
    (root / "model.pkl").write_bytes(b"opaque-test-bytes-never-deserialized")
    value["model_sha256"] = hashlib.sha256((root / "model.pkl").read_bytes()).hexdigest()
    write_json(
        root / "release.json",
        {
            "model_version": value["model_version"],
            "model_sha256": value["model_sha256"],
            "feature_contract_version": "1.1",
        },
    )
    write_json(
        root / "catalog.json",
        {"catalog_version": value["catalog_version"], "feature_contract_version": "1.1"},
    )
    write_json(root / "comparison.json", {"aggregate_only": True})
    for name in ("release", "catalog", "comparison"):
        value[name + "_sha256"] = hashlib.sha256((root / (name + ".json")).read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["model.pkl", "catalog.json", "release.json", "comparison.json"])
def test_bundle_checks_every_pinned_file_without_deserializing(tmp_path, manifest_data, name):
    make_bundle(tmp_path, manifest_data)
    preflight.bundle_identity(manifest_data, tmp_path)
    (tmp_path / name).write_bytes(b"corrupt")
    with pytest.raises(preflight.PreflightError, match="checksum mismatch"):
        preflight.bundle_identity(manifest_data, tmp_path)


def test_bundle_requires_consistent_declared_identities_even_with_valid_checksums(
    tmp_path, manifest_data
):
    make_bundle(tmp_path, manifest_data)
    write_json(
        tmp_path / "release.json",
        {
            "model_version": "wrong",
            "model_sha256": manifest_data["model_sha256"],
            "feature_contract_version": "1.1",
        },
    )
    manifest_data["release_sha256"] = hashlib.sha256(
        (tmp_path / "release.json").read_bytes()
    ).hexdigest()
    with pytest.raises(preflight.PreflightError, match="identities differ"):
        preflight.bundle_identity(manifest_data, tmp_path)


def make_backup(root, stamp):
    backup = root / stamp.strftime("backup-%Y%m%dT%H%M%S.%fZ")
    backup.mkdir(parents=True)
    (backup / "objects").mkdir()
    (backup / "database.dump").write_bytes(b"synthetic-dump")
    write_json(
        backup / "manifest.json",
        {
            "format": "risklab-backup/1",
            "backup_id": backup.name,
            "files": backup_manifest.inventory(backup),
        },
    )
    return backup


def test_backup_selection_checks_inventory_and_ignores_invalid_newer_directories(tmp_path):
    now = datetime(2026, 11, 13, tzinfo=UTC)
    valid = make_backup(tmp_path, now - timedelta(minutes=5))
    (tmp_path / "backup-20990101T000000.000000Z").mkdir()
    preflight.recent_backup(tmp_path, now=now, max_age=3600)
    (valid / "database.dump").write_bytes(b"tampered")
    with pytest.raises(preflight.PreflightError, match="No fresh"):
        preflight.recent_backup(tmp_path, now=now, max_age=3600)


@pytest.mark.parametrize("bad_manifest", [[], None, {"format": "risklab-backup/1", "files": []}])
def test_malformed_newer_backup_cannot_hide_an_older_verified_copy(tmp_path, bad_manifest):
    now = datetime(2026, 11, 13, tzinfo=UTC)
    make_backup(tmp_path, now - timedelta(minutes=5))
    newer = make_backup(tmp_path, now - timedelta(minutes=1))
    write_json(newer / "manifest.json", bad_manifest)
    with pytest.raises(ValueError, match="manifest/inventory mismatch"):
        backup_manifest.verify(newer)
    preflight.recent_backup(tmp_path, now=now, max_age=3600)


@pytest.mark.parametrize("age", [0, -1, 86401])
def test_backup_age_policy_is_bounded(tmp_path, age):
    with pytest.raises(preflight.PreflightError, match="BACKUP_MAX_AGE_SECONDS"):
        preflight.recent_backup(tmp_path, now=datetime.now(UTC), max_age=age)


def test_backup_symlink_and_empty_dump_are_not_valid_copies(tmp_path):
    now = datetime(2026, 11, 13, tzinfo=UTC)
    valid = make_backup(tmp_path / "actual", now)
    (valid / "database.dump").write_bytes(b"")
    write_json(
        valid / "manifest.json",
        {
            "format": "risklab-backup/1",
            "backup_id": valid.name,
            "files": backup_manifest.inventory(valid),
        },
    )
    with pytest.raises(preflight.PreflightError, match="No fresh"):
        preflight.recent_backup(valid.parent, now=now, max_age=3600)
    link = tmp_path / "linked"
    link.symlink_to(valid.parent, target_is_directory=True)
    with pytest.raises(preflight.PreflightError, match="regular local directory"):
        preflight.recent_backup(link, now=now, max_age=3600)


@pytest.mark.parametrize("offset", [timedelta(days=2), timedelta(minutes=-10)])
def test_stale_and_future_backups_do_not_pass(tmp_path, offset):
    now = datetime(2026, 11, 13, tzinfo=UTC)
    make_backup(tmp_path, now - offset)
    with pytest.raises(preflight.PreflightError, match="No fresh"):
        preflight.recent_backup(tmp_path, now=now, max_age=3600)


def test_execute_refuses_before_reading_inputs_or_spawning_commands(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Executed a side effect")

    monkeypatch.setattr(preflight, "manifest", forbidden)
    monkeypatch.setattr(preflight.subprocess, "run", forbidden)
    assert preflight.main(["--execute"]) == 2
    assert "unavailable" in capsys.readouterr().out


def test_optional_checks_and_example_are_explicit_not_deployment_success(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "compose_config", lambda *args, **kwargs: None)
    for key in ("RELEASE_MANIFEST", "RELEASE_DIR", "BACKUP_ROOT"):
        monkeypatch.delenv(key, raising=False)
    assert preflight.main(["--dry-run", "--example"]) == 0
    output = capsys.readouterr().out
    assert "EXAMPLE ONLY" in output and output.count("NOT CHECKED") == 2
    assert "No SSH/pull/migration/traffic changes" in output


def test_invalid_private_inputs_do_not_print_raw_values(monkeypatch, tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text('{"private":"a-sensitive-value"}')
    monkeypatch.setenv("RELEASE_MANIFEST", str(path))
    assert preflight.main(["--dry-run"]) == 2
    assert "a-sensitive-value" not in capsys.readouterr().out


@pytest.mark.parametrize("trusted", ["", "*", "0.0.0.0/0", "::/0", "invalid-peer", "127.0.0.1,*"])
def test_proxy_trust_requires_explicit_bounded_ips(monkeypatch, manifest_data, trusted):
    def must_not_run(*args, **kwargs):
        pytest.fail("Unsafe proxy trust must fail before Compose")

    monkeypatch.setattr(preflight.subprocess, "run", must_not_run)
    with pytest.raises(preflight.PreflightError, match="trusted Caddy"):
        preflight.compose_config(
            manifest_data,
            {
                "RISKLAB_ORIGIN": "https://risklab.test",
                "SITE_ADDRESS": "risklab.test",
                "RISKLAB_TRUSTED_PROXY_IPS": trusted,
            },
            example=False,
        )
