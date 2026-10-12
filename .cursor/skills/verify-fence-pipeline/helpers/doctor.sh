#!/usr/bin/env bash
set -euo pipefail

_DIR=$(dirname "$0")
_HERE=$(cd "$_DIR" && pwd)
ROOT="$(cd "$_HERE/../../../.." && pwd)"
cd "$ROOT"

ok=1

if ! docker compose exec -T db pg_isready -U fence -d fence >/dev/null 2>&1; then
  echo "FAIL: Postgres not ready (docker compose up -d?)"
  ok=0
else
  echo "OK: Postgres ready"
fi

exists="$(docker compose exec -T db psql -U fence -d fence -tAc \
  "SELECT 1 FROM pg_database WHERE datname='fence_verify'" 2>/dev/null || true)"
if [[ "${exists//[[:space:]]/}" != "1" ]]; then
  echo "FAIL: fence_verify missing (run helpers/ensure-verify-db.sh)"
  ok=0
else
  echo "OK: fence_verify exists"
fi

if ! command -v fence-pipeline >/dev/null 2>&1; then
  if ! python -c "import fence_pipeline" 2>/dev/null; then
    echo "FAIL: fence_pipeline not installed (pip install -e '.[dev]')"
    ok=0
  else
    echo "OK: fence_pipeline importable via python"
  fi
else
  echo "OK: fence-pipeline on PATH"
  fence-pipeline run --help >/dev/null
fi

for f in tests/fixtures/receivables.csv tests/fixtures/collections.csv; do
  if [[ ! -f "$f" ]]; then
    echo "FAIL: missing $f"
    ok=0
  else
    echo "OK: $f"
  fi
done

if [[ "$ok" -ne 1 ]]; then
  exit 1
fi
echo "doctor: PASS"
