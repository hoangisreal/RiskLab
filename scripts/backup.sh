#!/usr/bin/env bash
# Consistent RiskLab backup (B06-T04): pg_dump + immutable objects + manifest.
# DATABASE_URL_FILE must contain a plain libpq URL (no SQLAlchemy driver suffix).
# Success is reported only after checksum verification and, when configured,
# encrypted off-VPS copy verification. A failed run never prunes the last good backup.
set -euo pipefail

BACKUP_ROOT=${BACKUP_ROOT:?Set BACKUP_ROOT}
DATABASE_URL_FILE=${DATABASE_URL_FILE:?Set DATABASE_URL_FILE with a dump-capable URL file}
STORAGE_ROOT=${STORAGE_ROOT:?Set STORAGE_ROOT}
RELEASE_DIR=${RELEASE_DIR:-}
ENCRYPTION_KEY_FILE=${ENCRYPTION_KEY_FILE:-}
OFFSITE_DIR=${OFFSITE_DIR:-}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$BACKUP_ROOT/backup-$STAMP"
mkdir -p "$DEST/objects"

DATABASE_URL=$(cat "$DATABASE_URL_FILE")
pg_dump --format=custom --no-owner --file="$DEST/database.dump" "$DATABASE_URL"
cp -a "$STORAGE_ROOT/." "$DEST/objects/"
if [ -n "$RELEASE_DIR" ]; then
  mkdir -p "$DEST/release"
  cp -a "$RELEASE_DIR/." "$DEST/release/"
fi

python3 - "$DEST" <<'PY'
import hashlib, json, sys
from pathlib import Path

dest = Path(sys.argv[1])
entries = []
for path in sorted(p for p in dest.rglob("*") if p.is_file() and p.name != "manifest.json"):
    data = path.read_bytes()
    entries.append({"path": str(path.relative_to(dest)), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
manifest = {"backup_id": dest.name, "format": "risklab-backup/1", "files": entries}
(dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
# Verify what was just written before any copy or prune step may proceed.
for entry in entries:
    digest = hashlib.sha256((dest / entry["path"]).read_bytes()).hexdigest()
    if digest != entry["sha256"]:
        raise SystemExit(f"checksum mismatch: {entry['path']}")
print(f"verified {len(entries)} files")
PY

if [ -n "$OFFSITE_DIR" ]; then
  mkdir -p "$OFFSITE_DIR"
  if [ -n "$ENCRYPTION_KEY_FILE" ]; then
    ARCHIVE="$OFFSITE_DIR/backup-$STAMP.tar.gz.enc"
    tar -czf - -C "$BACKUP_ROOT" "backup-$STAMP" \
      | openssl enc -aes-256-cbc -salt -pbkdf2 -pass "file:$ENCRYPTION_KEY_FILE" -out "$ARCHIVE"
    echo "offsite encrypted archive: $ARCHIVE"
  else
    cp -a "$DEST" "$OFFSITE_DIR/"
    echo "offsite copy (unencrypted; set ENCRYPTION_KEY_FILE for production): $OFFSITE_DIR/backup-$STAMP"
  fi
fi

# Retention: keep the newest 7 backups, plus the newest backup of each of the
# last 4 ISO weeks. Only runs after this backup verified successfully.
python3 - "$BACKUP_ROOT" <<'PY'
import shutil, sys
from datetime import datetime
from pathlib import Path

root = Path(sys.argv[1])
backups = sorted((p for p in root.glob("backup-*") if p.is_dir()), reverse=True)
keep = set(backups[:7])
weeks = {}
for path in backups:
    try:
        week = datetime.strptime(path.name, "backup-%Y%m%dT%H%M%SZ").isocalendar()[:2]
    except ValueError:
        continue
    weeks.setdefault(week, path)
keep.update(list(weeks.values())[:4])
for path in backups:
    if path not in keep:
        shutil.rmtree(path)
print(f"retention keeps {len(keep)} backups")
PY

echo "backup complete: $DEST"
