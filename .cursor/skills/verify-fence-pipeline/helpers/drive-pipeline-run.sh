#!/usr/bin/env bash
# Happy-path drive for feature: pipeline-run
set -euo pipefail

_DIR=$(dirname "$0")
_HERE=$(cd "$_DIR" && pwd)
HELPERS="$_HERE"
# shellcheck source=/dev/null
source "$HELPERS/verify-env.sh"

"$HELPERS/ensure-verify-db.sh"
"$HELPERS/doctor.sh"

FENCE_VERIFY_DATABASE_URL="$FENCE_VERIFY_DATABASE_URL" "$HELPERS/cleanup.sh"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVDIR="$ROOT/.cursor/skills/verify-fence-pipeline/evidence/$STAMP"
mkdir -p "$EVDIR/out"
export EVDIR

{
  echo "=== fence-pipeline run (fixtures) ==="
  fence-pipeline run \
    --receivables tests/fixtures/receivables.csv \
    --collections tests/fixtures/collections.csv \
    --output-dir "$EVDIR/out" \
    --database-url "$FENCE_VERIFY_DATABASE_URL"
} 2>&1 | tee "$EVDIR/cli.log"

run_id="$(grep -E '^run_id=' "$EVDIR/cli.log" | tail -1 | cut -d= -f2)"
if [[ -z "$run_id" ]]; then
  echo "ASSERT FAIL: no run_id in cli.log"
  exit 1
fi
echo "run_id=$run_id" | tee -a "$EVDIR/assertions.txt"

schedule="$EVDIR/out/payment_schedule_report_run_${run_id}.csv"
position="$EVDIR/out/receivables_position_report_run_${run_id}.csv"
pool="$EVDIR/out/pool_report_run_${run_id}.csv"

for f in "$schedule" "$position" "$pool"; do
  if [[ ! -f "$f" ]]; then
    echo "ASSERT FAIL: missing $f" | tee -a "$EVDIR/assertions.txt"
    exit 1
  fi
  base="$(basename "$f")"
  echo "OK file $base" | tee -a "$EVDIR/assertions.txt"
done

if ! grep -q 'P-TEST-1' "$schedule"; then
  echo "ASSERT FAIL: schedule missing P-TEST-1" | tee -a "$EVDIR/assertions.txt"
  exit 1
fi
if ! grep -q 'P-TEST-2' "$schedule"; then
  echo "ASSERT FAIL: schedule missing P-TEST-2" | tee -a "$EVDIR/assertions.txt"
  exit 1
fi
echo "OK schedule payment_ids" | tee -a "$EVDIR/assertions.txt"

if ! grep -q 'SL-TEST-1' "$position"; then
  echo "ASSERT FAIL: position missing SL-TEST-1" | tee -a "$EVDIR/assertions.txt"
  exit 1
fi
if ! grep -q 'Current' "$position"; then
  echo "ASSERT FAIL: expected Current status" | tee -a "$EVDIR/assertions.txt"
  exit 1
fi
echo "OK position status" | tee -a "$EVDIR/assertions.txt"

if ! grep -q 'borrowing_base' "$pool"; then
  echo "ASSERT FAIL: pool missing borrowing_base" | tee -a "$EVDIR/assertions.txt"
  exit 1
fi
echo "OK pool metrics" | tee -a "$EVDIR/assertions.txt"

"$HELPERS/snapshot-db.sh"

echo "PROOF_OK evidence=$EVDIR" | tee -a "$EVDIR/assertions.txt"
