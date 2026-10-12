#!/usr/bin/env bash
# Source from repo root: source .cursor/skills/verify-fence-pipeline/helpers/verify-env.sh
set -euo pipefail

_DIR=$(dirname "${BASH_SOURCE[0]}")
_HERE=$(cd "$_DIR" && pwd)
ROOT="$(cd "$_HERE/../../../.." && pwd)"
cd "$ROOT"

export FENCE_VERIFY_DATABASE_URL="${FENCE_VERIFY_DATABASE_URL:-postgresql://fence:fence@localhost:5432/fence_verify}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVDIR="${EVDIR:-$ROOT/.cursor/skills/verify-fence-pipeline/evidence/$STAMP}"
mkdir -p "$EVDIR/out"
export EVDIR
export ROOT

echo "ROOT=$ROOT"
echo "FENCE_VERIFY_DATABASE_URL=$FENCE_VERIFY_DATABASE_URL"
echo "EVDIR=$EVDIR"
