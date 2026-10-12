#!/usr/bin/env bash
set -euo pipefail

_DIR=$(dirname "$0")
_HERE=$(cd "$_DIR" && pwd)
ROOT="$(cd "$_HERE/../../../.." && pwd)"
cd "$ROOT"

DSN="${FENCE_VERIFY_DATABASE_URL:-postgresql://fence:fence@localhost:5432/fence_verify}"

if [[ "$DSN" != *"/fence_verify"* ]]; then
  echo "REFUSE: cleanup only truncates fence_verify (got $DSN)"
  exit 1
fi

if docker compose exec -T db pg_isready -U fence -d fence_verify >/dev/null 2>&1; then
  docker compose exec -T db psql -U fence -d fence_verify -v ON_ERROR_STOP=1 <<'SQL' || true
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pipeline_runs') THEN
    TRUNCATE TABLE
      payment_allocations,
      payments_applied,
      installments,
      eligibility_results,
      sector_concentration,
      pool_snapshots,
      row_exceptions,
      receivables_current,
      payment_events,
      receivable_events,
      pipeline_runs
    CASCADE;
  END IF;
END $$;
SQL
  echo "truncated fence_verify tables (if present)"
else
  echo "skip truncate: fence_verify not reachable"
fi

rm -rf /tmp/fence-pipeline-verify-* 2>/dev/null || true
echo "cleanup done (evidence/ preserved)"
