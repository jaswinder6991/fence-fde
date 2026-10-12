#!/usr/bin/env bash
set -euo pipefail

_DIR=$(dirname "$0")
_HERE=$(cd "$_DIR" && pwd)
ROOT="$(cd "$_HERE/../../../.." && pwd)"
cd "$ROOT"

: "${EVDIR:?source verify-env.sh first}"

OUT="$EVDIR/db-snapshot.txt"
{
  echo "database=fence_verify"
  echo "--- counts ---"
  docker compose exec -T db psql -U fence -d fence_verify -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'pipeline_runs' AS t, COUNT(*)::text FROM pipeline_runs
UNION ALL SELECT 'payment_events', COUNT(*)::text FROM payment_events
UNION ALL SELECT 'payments_applied', COUNT(*)::text FROM payments_applied
UNION ALL SELECT 'payment_allocations', COUNT(*)::text FROM payment_allocations
UNION ALL SELECT 'installments', COUNT(*)::text FROM installments
UNION ALL SELECT 'eligibility_results', COUNT(*)::text FROM eligibility_results
UNION ALL SELECT 'pool_snapshots', COUNT(*)::text FROM pool_snapshots
ORDER BY 1;
SQL
  echo "--- sample allocations ---"
  docker compose exec -T db psql -U fence -d fence_verify -v ON_ERROR_STOP=1 <<'SQL'
SELECT payment_id, receivable_external_id, installment_seq, amount
FROM payment_allocations
ORDER BY receivable_external_id, installment_seq, payment_id
LIMIT 20;
SQL
} >"$OUT"

echo "wrote $OUT"
