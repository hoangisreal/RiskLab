"""Read-only, fail-closed release preflight; never a deployment authorization.

Keep this helper stdlib-only so the operator can validate a release before any
application dependencies or artifact deserialization. Runtime/bundle parity and
database schema compatibility must still be verified inside the pinned image.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from scripts.backup_manifest import verify as verify_backup

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {
    "format",
    "git_commit",
    "api_image",
    "web_image",
    "schema_revision",
    "model_version",
    "model_sha256",
    "catalog_version",
    "catalog_sha256",
    "release_sha256",
    "comparison_sha256",
}
IMAGE = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}"
)


class PreflightError(ValueError):
    """Only deliberately safe diagnostics may reach the operator's logs."""


def regular_file(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise PreflightError("Required input must be a regular file without symlink ancestors")


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PreflightError("Duplicate JSON keys are not allowed")
        result[key] = value
    return result


def invalid_constant(_value: str) -> None:
    raise PreflightError("Non-finite JSON values are not allowed")


def read_json(path: Path, *, max_bytes: int = 2_000_000) -> dict:
    regular_file(path)
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PreflightError("JSON metadata exceeds the size limit")
    try:
        result = json.loads(
            data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant
        )
    except (ValueError, RecursionError):
        raise PreflightError("Invalid JSON metadata") from None
    if not isinstance(result, dict):
        raise PreflightError("JSON metadata must be an object")
    return result


def manifest(path: Path, *, example: bool = False) -> dict[str, str]:
    value = read_json(path)
    if set(value) != FIELDS or any(not isinstance(v, str) for v in value.values()):
        raise PreflightError("Release manifest fields do not match risklab-release/2")
    if value["format"] != "risklab-release/2":
        raise PreflightError("Expected risklab-release/2; v1 does not pin all bundle metadata")
    for name in ("api_image", "web_image"):
        if IMAGE.fullmatch(value[name]) is None:
            raise PreflightError(f"{name} must be a registry/repository pinned by SHA256 digest")
    for name, length in (
        ("git_commit", 40),
        ("model_sha256", 64),
        ("catalog_sha256", 64),
        ("release_sha256", 64),
        ("comparison_sha256", 64),
    ):
        if re.fullmatch(rf"[0-9a-f]{{{length}}}", value[name]) is None:
            raise PreflightError(f"Invalid {name}")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value["schema_revision"]) is None:
        raise PreflightError("Invalid schema_revision")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["catalog_version"]) is None:
        raise PreflightError("Invalid catalog_version")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value["model_version"]) is None:
        raise PreflightError("Invalid model_version")
    if not example:
        hashes = [
            value[name]
            for name in (
                "git_commit",
                "model_sha256",
                "catalog_sha256",
                "release_sha256",
                "comparison_sha256",
            )
        ]
        hashes.extend(value[name].rsplit(":", 1)[1] for name in ("api_image", "web_image"))
        template_image = any(
            "example" in value[name].split("@", 1)[0].split("/")
            or value[name].startswith("example.invalid/")
            for name in ("api_image", "web_image")
        )
        if (
            any(set(digest) == {"0"} for digest in hashes)
            or template_image
            or value["model_version"] == "operator-pinned-model-version"
        ):
            raise PreflightError(
                "Template placeholders are not a release; use --example for template checks"
            )
    return value


def deployment_origin(env: dict[str, str], *, example: bool) -> tuple[str, str]:
    origin = env.get("RISKLAB_ORIGIN", "https://example.invalid" if example else "")
    site = env.get("SITE_ADDRESS", "example.invalid" if example else "")
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
            or parsed.netloc != site
            or re.fullmatch(r"[a-z0-9.-]+(?::443)?", site) is None
        ):
            raise ValueError
    except ValueError:
        raise PreflightError(
            "Set a matching HTTPS RISKLAB_ORIGIN and SITE_ADDRESS without credentials/path"
        ) from None
    return origin, site


def compose_config(value: dict[str, str], env: dict[str, str], *, example: bool) -> None:
    origin, site = deployment_origin(env, example=example)
    # Ambient COMPOSE_* and automatic .env loading must not change the checked project.
    effective = {k: v for k, v in env.items() if not k.startswith("COMPOSE_")}
    trusted = env.get("RISKLAB_TRUSTED_PROXY_IPS", "127.0.0.1" if example else "")
    try:
        if not trusted or any(
            ipaddress.ip_network(peer.strip(), strict=False).prefixlen == 0
            for peer in trusted.split(",")
        ):
            raise ValueError
    except ValueError:
        raise PreflightError(
            "Set explicit trusted Caddy IPs/CIDRs; wildcard/all-address trust is invalid"
        ) from None
    effective["RISKLAB_TRUSTED_PROXY_IPS"] = trusted
    effective.update(
        RISKLAB_API_IMAGE=value["api_image"],
        RISKLAB_WEB_IMAGE=value["web_image"],
        RISKLAB_CATALOG_SHA256=value["catalog_sha256"],
        RISKLAB_ORIGIN=origin,
        SITE_ADDRESS=site,
        COMPOSE_DISABLE_ENV_FILE="1",
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "--project-directory",
                str(ROOT / "deploy"),
                "-f",
                str(ROOT / "deploy/compose.prod.yml"),
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=effective,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreflightError("Docker Compose config is required and did not complete") from None
    if result.returncode != 0:
        # Compose diagnostics may contain interpolated values; do not forward them.
        raise PreflightError("Docker Compose rejected the production configuration")
    try:
        services = json.loads(result.stdout)["services"]
        if services["api"]["environment"].get("FORWARDED_ALLOW_IPS") != trusted:
            raise ValueError
        if services["caddy"]["environment"].get("SITE_ADDRESS") != site:
            raise ValueError
        for name in ("api", "worker"):
            if services[name]["environment"].get("RISKLAB_ORIGIN") != origin:
                raise ValueError
        for name in ("api", "worker", "migrate"):
            if services[name]["image"] != value["api_image"]:
                raise ValueError
        if services["web"]["image"] != value["web_image"]:
            raise ValueError
        for name in ("api", "worker"):
            if services[name]["environment"]["RISKLAB_CATALOG_SHA256"] != value["catalog_sha256"]:
                raise ValueError
        if services["migrate"]["command"] != ["/app/.venv/bin/alembic", "upgrade", "head"]:
            raise ValueError
        if services["worker"]["command"] != ["/app/.venv/bin/risklab-worker"]:
            raise ValueError
        if any(service.get("ports") for name, service in services.items() if name != "caddy"):
            raise ValueError
        if any(service.get("network_mode") == "host" for service in services.values()):
            raise ValueError
        ingress = services["caddy"]["ports"]
        if len(ingress) != 2 or {
            (str(port["published"]), port["target"], port.get("protocol", "tcp"))
            for port in ingress
        } != {("80", 80, "tcp"), ("443", 443, "tcp")}:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PreflightError(
            "Rendered Compose does not match release images, catalog, commands or private ports"
        ) from None


def bundle_identity(value: dict[str, str], root: Path) -> None:
    # No pickle loading: an operator-controlled digest is not a sandbox.
    for name, key in (
        ("model.pkl", "model_sha256"),
        ("catalog.json", "catalog_sha256"),
        ("release.json", "release_sha256"),
        ("comparison.json", "comparison_sha256"),
    ):
        path = root / name
        regular_file(path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != value[key]:
            raise PreflightError(f"Bundle checksum mismatch for {name}")
    release = read_json(root / "release.json")
    catalog = read_json(root / "catalog.json", max_bytes=20_000_000)
    if (
        release.get("model_version") != value["model_version"]
        or release.get("model_sha256") != value["model_sha256"]
        or catalog.get("catalog_version") != value["catalog_version"]
        or release.get("feature_contract_version") != catalog.get("feature_contract_version")
        or not release.get("feature_contract_version")
    ):
        raise PreflightError("Bundle model/catalog identities differ from the manifest")


def recent_backup(root: Path, *, now: datetime, max_age: int) -> None:
    if not 1 <= max_age <= 86400:
        raise PreflightError("BACKUP_MAX_AGE_SECONDS must be between 1 and 86400")
    if root.is_symlink() or any(p.is_symlink() for p in root.parents) or not root.is_dir():
        raise PreflightError("Backup root must be a regular local directory")
    for path in sorted(root.glob("backup-*"), reverse=True):
        try:
            stamp = datetime.strptime(path.name, "backup-%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
            age = (now - stamp).total_seconds()
            if not 0 <= age <= max_age:
                continue
            verified = verify_backup(path)
            if (
                verified.get("backup_id") == path.name
                and (path / "database.dump").stat().st_size > 0
            ):
                return
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            continue
    raise PreflightError("No fresh, complete, checksum-verified backup found")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only RiskLab release preflight (not deploy)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--example", action="store_true", help="Check template only, never a real release"
    )
    args = parser.parse_args(argv)
    if args.execute:
        print(
            "Automated --execute is unavailable: "
            "authorized release/rollback rehearsal is still required"
        )
        return 2
    try:
        env = dict(os.environ)
        path = Path(env.get("RELEASE_MANIFEST", str(ROOT / "deploy/release-manifest.example.json")))
        value = manifest(path, example=args.example)
        compose_config(value, env, example=args.example)
        checks = ["manifest", "compose"]
        if env.get("RELEASE_DIR"):
            bundle_identity(value, Path(env["RELEASE_DIR"]))
            checks.append("bundle bytes/identity")
        if env.get("BACKUP_ROOT"):
            recent_backup(
                Path(env["BACKUP_ROOT"]),
                now=datetime.now(UTC),
                max_age=int(env.get("BACKUP_MAX_AGE_SECONDS", "3600")),
            )
            checks.append("backup integrity/freshness")
        print(
            ("EXAMPLE ONLY" if args.example else "READ-ONLY PREFLIGHT")
            + ": checked "
            + ", ".join(checks)
        )
        if not env.get("RELEASE_DIR"):
            print("NOT CHECKED: bundle bytes/identity (set RELEASE_DIR)")
        if not env.get("BACKUP_ROOT"):
            print("NOT CHECKED: backup integrity/freshness (set BACKUP_ROOT)")
        print(
            "Not deployment-ready evidence: image availability/runtime, schema, secrets, "
            "maintenance, TLS and rollback are unverified"
        )
        print("No SSH/pull/migration/traffic changes performed")
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print(
            "Preflight failed: "
            + (
                str(error)
                if isinstance(error, PreflightError)
                else "Invalid or unavailable private inputs"
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
