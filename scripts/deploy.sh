#!/usr/bin/env bash
# Release deploy rehearsal/runner (B06-T03). Defaults to a dry-run preflight.
# A real deploy needs --execute plus DEPLOY_HOST/DEPLOY_USER and a pinned
# release manifest; SSH host-key verification is never disabled, and the
# database is never auto-downgraded on rollback.
set -euo pipefail

MODE=dry-run
MANIFEST=${RELEASE_MANIFEST:-deploy/release-manifest.example.json}
for arg in "$@"; do
  case "$arg" in
    --execute) MODE=execute ;;
    --dry-run) MODE=dry-run ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

echo "mode: $MODE"
echo "manifest: $MANIFEST"
python3 - "$MANIFEST" <<'PY'
import json, re, sys

manifest = json.load(open(sys.argv[1]))
if manifest.get("format") != "risklab-release/1":
    raise SystemExit("unsupported release manifest format")
for key in ("api_image", "web_image"):
    if "@sha256:" not in str(manifest.get(key, "")):
        raise SystemExit(f"{key} must pin an image digest")
if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("git_commit", ""))):
    raise SystemExit("git_commit must be a full commit SHA")
if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("model_sha256", ""))):
    raise SystemExit("model_sha256 must be a SHA256")
print("manifest ok:", manifest["git_commit"], manifest["schema_revision"])
PY

test -f deploy/compose.prod.yml && echo "compose file present"
test -f deploy/Caddyfile && echo "caddy file present"
if command -v docker >/dev/null; then
  RISKLAB_API_IMAGE=example.invalid/api@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  RISKLAB_WEB_IMAGE=example.invalid/web@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  RISKLAB_ORIGIN=https://example.invalid SITE_ADDRESS=example.invalid \
    docker compose -f deploy/compose.prod.yml config --quiet \
    && echo "compose config valid"
fi

if [ -n "${BACKUP_ROOT:-}" ]; then
  latest=$(ls -d "$BACKUP_ROOT"/backup-* 2>/dev/null | sort | tail -n 1 || true)
  echo "latest backup: ${latest:-none}"
fi

if [ "$MODE" = dry-run ]; then
  echo "dry-run only: no host contacted, no migration run, no traffic changed"
  echo "execute path requires: backup freshness check, maintenance window, one-shot migrate, health + synthetic smoke, then reopen writes"
  exit 0
fi

: "${DEPLOY_HOST:?Set DEPLOY_HOST for --execute}"
: "${DEPLOY_USER:?Set DEPLOY_USER for --execute}"
ssh -o StrictHostKeyChecking=yes "$DEPLOY_USER@$DEPLOY_HOST" \
  "cd /opt/risklab && docker compose -f deploy/compose.prod.yml pull && docker compose -f deploy/compose.prod.yml run --rm migrate && docker compose -f deploy/compose.prod.yml up -d && curl -fsS http://127.0.0.1/api/v1/health/ready || curl -fsS https://\$SITE_ADDRESS/api/v1/health/ready"
echo "deploy command finished; record release ID, digests and smoke result in the runbook"
