#!/usr/bin/env bash
# Restore only into an empty, isolated target; never drop existing application data.
set -euo pipefail
umask 077
HELPER="$(cd -- "$(dirname -- "$0")" && pwd)/backup_manifest.py"
: "${BACKUP_DIR:?Set BACKUP_DIR}" "${TARGET_DATABASE_URL_FILE:?Set TARGET_DATABASE_URL_FILE}" "${TARGET_STORAGE_ROOT:?Set TARGET_STORAGE_ROOT}"
python3 "$HELPER" verify "$BACKUP_DIR"
python3 - "$TARGET_STORAGE_ROOT" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
if any(q.is_symlink() for q in (p, *p.parents)) or (p.exists() and (not p.is_dir() or any(p.iterdir()))):
    raise SystemExit("Restore storage must be absent or empty and not symlinked")
PY
WORK=$(mktemp -d "${TMPDIR:-/tmp}/risklab-restore-XXXXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if ! COUNT=$(python3 "$HELPER" db "$TARGET_DATABASE_URL_FILE" psql -X --no-password -A -t --set ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'" 2>"$WORK/client.log"); then
  echo "Restore target inspection failed; no database changes were attempted" >&2
  exit 1
fi
if [ "$COUNT" != 0 ]; then
  echo "Restore refuses a nonempty database" >&2
  exit 1
fi
# Render SQL completely before touching target objects. A pipeline could let psql
# commit a truncated pg_restore stream before the producer's failure is known.
# Temporary SQL and diagnostics contain private data and are removed on exit.
if ! pg_restore --exit-on-error --no-owner --file="$WORK/restore.sql" "$BACKUP_DIR/database.dump" >"$WORK/client.log" 2>&1; then
  echo "Restore archive conversion failed; no database changes were attempted" >&2
  exit 1
fi
# Files precede DB references. They may remain for manual inspection after failure;
# never delete a target directory or database automatically to make retry succeed.
mkdir -p "$TARGET_STORAGE_ROOT"
cp -a "$BACKUP_DIR/objects/." "$TARGET_STORAGE_ROOT/"
cat >"$WORK/recovery.sql" <<'SQL'
-- pg_restore SQL clears search_path; qualify recovery tables in the same session.
DELETE FROM public.sessions;
UPDATE public.jobs SET state = 'QUEUED', lease_owner = NULL, lease_expires_at = NULL,
    available_at = now() WHERE state = 'RUNNING';
SQL
# Both files share one transaction: recovery errors cannot leave restored sessions
# active in a separately committed database. Neither SQL file may contain COMMIT.
if ! python3 "$HELPER" db "$TARGET_DATABASE_URL_FILE" psql -X --no-password --set ON_ERROR_STOP=1 \
  --single-transaction --file="$WORK/restore.sql" --file="$WORK/recovery.sql" >"$WORK/client.log" 2>&1; then
  echo "Restore transaction failed; keep the target isolated and inspect database/roles/backup privately" >&2
  exit 1
fi
echo "restore completed into the isolated target; sessions/leases recovered atomically; verify RBAC, counts and pinned release before use"
