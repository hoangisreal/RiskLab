#!/usr/bin/env bash
# Isolated restore rehearsal (B06-T04). Restores into the database named by
# TARGET_DATABASE_URL_FILE, which must be a fresh, non-production database.
# Sessions are revoked and RUNNING jobs return to lease recovery afterwards.
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:?Set BACKUP_DIR to one verified backup directory}
TARGET_DATABASE_URL_FILE=${TARGET_DATABASE_URL_FILE:?Set TARGET_DATABASE_URL_FILE for the fresh target DB}
TARGET_STORAGE_ROOT=${TARGET_STORAGE_ROOT:?Set TARGET_STORAGE_ROOT for restored objects}

python3 - "$BACKUP_DIR" <<'PY'
import hashlib, json, sys
from pathlib import Path

dest = Path(sys.argv[1])
manifest = json.loads((dest / "manifest.json").read_text())
for entry in manifest["files"]:
    path = dest / entry["path"]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise SystemExit(f"backup verification failed: {entry['path']}")
print(f"backup verified: {manifest['backup_id']} ({len(manifest['files'])} files)")
PY

TARGET_URL=$(cat "$TARGET_DATABASE_URL_FILE")
pg_restore --clean --if-exists --no-owner --dbname="$TARGET_URL" "$BACKUP_DIR/database.dump"
mkdir -p "$TARGET_STORAGE_ROOT"
cp -a "$BACKUP_DIR/objects/." "$TARGET_STORAGE_ROOT/"

# Restored sessions must not survive; running jobs re-enter lease recovery
# instead of completing under a dead worker's attempt.
psql "$TARGET_URL" --set ON_ERROR_STOP=1 <<'SQL'
DELETE FROM sessions;
UPDATE jobs
SET state = 'QUEUED', lease_owner = NULL, lease_expires_at = NULL,
    available_at = now()
WHERE state = 'RUNNING';
SQL

echo "restore complete into the target database; verify login/RBAC/counts before use"
