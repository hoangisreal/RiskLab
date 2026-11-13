"""Backup inventory verification and libpq execution without credential-bearing argv."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root: Path) -> list[dict[str, object]]:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ValueError("Backup contains a symlink or special file")
        if path.is_file() and path != root / "manifest.json":
            entries.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": digest(path),
                    "bytes": path.stat().st_size,
                }
            )
    if not any(e["path"] == "database.dump" for e in entries):
        raise ValueError("Backup has no database dump")
    if not (root / "objects").is_dir():
        raise ValueError("Backup has no object inventory")
    return entries


def verify(root: Path) -> dict:
    if root.is_symlink() or (root / "manifest.json").is_symlink():
        raise ValueError("Backup manifest must be a regular local file")
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "risklab-backup/1"
        or manifest.get("files") != inventory(root)
    ):
        raise ValueError("Backup manifest/inventory mismatch")
    return manifest


def database_command(url_file: Path, argv: list[str]) -> int:
    parsed = urlsplit(url_file.read_text().strip())
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or not parsed.hostname
        or not parsed.path[1:]
    ):
        raise ValueError("Expected a plain PostgreSQL URL with host and database")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(
        PGHOST=parsed.hostname,
        PGPORT=str(parsed.port or 5432),
        PGDATABASE=unquote(parsed.path[1:]),
        PGUSER=unquote(parsed.username or ""),
        PGPASSWORD=unquote(parsed.password or ""),
        PGCONNECT_TIMEOUT="10",
    )
    options = {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
    }
    for key, value in parse_qsl(parsed.query):
        if key not in options:
            raise ValueError("Unsupported database URL option")
        env[options[key]] = value
    if argv[0] == "pg_restore":
        argv = [argv[0], "--dbname=" + env["PGDATABASE"], *argv[1:]]
    return subprocess.run(argv, env=env, check=False).returncode


def main() -> None:
    command, location, *args = sys.argv[1:]
    root = Path(location)
    if command == "db":
        raise SystemExit(database_command(root, args))
    if command == "create":
        manifest = {"format": "risklab-backup/1", "backup_id": args[0], "files": inventory(root)}
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    elif command == "verify":
        verify(root)
    elif command == "retain":
        import shutil
        from datetime import datetime

        backups = []
        for path in sorted(root.glob("backup-*"), reverse=True):
            try:
                stamp = datetime.strptime(path.name, "backup-%Y%m%dT%H%M%S.%fZ")
                verify(path)
            except (OSError, ValueError, KeyError):
                continue  # An incomplete backup is never a retention replacement.
            backups.append((path, stamp.isocalendar()[:2]))
        keep = {path for path, _ in backups[:7]}
        weekly: dict[tuple[int, int], Path] = {}
        for path, week in backups:
            weekly.setdefault(week, path)
        keep.update(list(weekly.values())[:4])
        for path, _ in backups:
            if path not in keep:
                shutil.rmtree(path)
    else:
        raise ValueError("Unknown backup operation")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError):
        raise SystemExit("Backup validation failed; inspect the private inputs") from None
