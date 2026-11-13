#!/usr/bin/env bash
# Publish only a verified backup; incomplete runs cannot displace retention copies.
set -euo pipefail
umask 077
HELPER="$(cd -- "$(dirname -- "$0")" && pwd)/backup_manifest.py"
: "${BACKUP_ROOT:?Set BACKUP_ROOT}" "${DATABASE_URL_FILE:?Set DATABASE_URL_FILE}" "${STORAGE_ROOT:?Set STORAGE_ROOT}"
if [ -n "${OFFSITE_DIR:-}" ]; then
  : "${ENCRYPTION_KEY_FILE:?Offsite backups require an encryption key file}"
fi
mkdir -p "$BACKUP_ROOT"
# Serialize backup and retention on this destination, including same-second runs.
exec 9>"$BACKUP_ROOT/.backup.lock"
flock -n 9
STAMP=$(date -u +%Y%m%dT%H%M%S.%6NZ)
DEST="$BACKUP_ROOT/backup-$STAMP"
WORK=$(mktemp -d "$BACKUP_ROOT/.pending-XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
mkdir -p "$WORK/backup/objects"
python3 "$HELPER" db "$DATABASE_URL_FILE" pg_dump --format=custom --no-owner --file="$WORK/backup/database.dump"
cp -a "$STORAGE_ROOT/." "$WORK/backup/objects/"
if [ -n "${RELEASE_DIR:-}" ]; then
  mkdir "$WORK/backup/release"
  cp -a "$RELEASE_DIR/." "$WORK/backup/release/"
fi
python3 "$HELPER" create "$WORK/backup" "backup-$STAMP"
python3 "$HELPER" verify "$WORK/backup"
# Preserve the verified local copy even if encryption or offsite transport fails.
mv "$WORK/backup" "$DEST"
python3 "$HELPER" retain "$BACKUP_ROOT"
if [ -n "${OFFSITE_DIR:-}" ]; then
  mkdir -p "$OFFSITE_DIR"
  tar -czf "$WORK/archive.tar.gz" --transform="s|^$(basename "$DEST")|backup|" -C "$BACKUP_ROOT" "$(basename "$DEST")"
  openssl enc -aes-256-cbc -salt -pbkdf2 -pass "file:$ENCRYPTION_KEY_FILE" \
    -in "$WORK/archive.tar.gz" -out "$WORK/archive.enc"
  openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$ENCRYPTION_KEY_FILE" \
    -in "$WORK/archive.enc" -out "$WORK/roundtrip.tar.gz"
  cmp "$WORK/archive.tar.gz" "$WORK/roundtrip.tar.gz"
  cp "$WORK/archive.enc" "$OFFSITE_DIR/.backup-$STAMP.pending"
  cmp "$WORK/archive.enc" "$OFFSITE_DIR/.backup-$STAMP.pending"
  mv "$OFFSITE_DIR/.backup-$STAMP.pending" "$OFFSITE_DIR/backup-$STAMP.tar.gz.enc"
fi
echo "backup complete: $DEST"
