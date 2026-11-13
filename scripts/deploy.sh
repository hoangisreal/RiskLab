#!/usr/bin/env bash
# Read-only release preflight. No SSH, pull, migration, traffic or volume mutation.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd -- "$ROOT"
exec python3 -m scripts.release_preflight "$@"
