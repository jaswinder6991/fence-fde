# Soft-fail and payment idempotency

Bad CSV rows become exceptions without aborting the run. Re-running the same
collections file must not double-apply payments (`payment_id` insert-once +
`payments_applied`).

## Sub-features

- Parse / allocate / reconcile issues → `row_exceptions`, `exceptions_report_run_*.csv`, and pool HTML
- Outstanding mismatch → `source=reconcile` exception (Starfleet should resend a corrected export)
- `ON CONFLICT DO NOTHING` on `payment_events.payment_id`
- Second CLI run: installment `amount_applied` unchanged

## How to get to it (user POV)

1. Run once with fixtures (or full data with known bad score rows).
2. Run again with the same files and same verify DB.
3. Confirm schedule amounts and DB allocations did not grow.

## Driving it with the CLI harness

```bash
source .cursor/skills/verify-fence-pipeline/helpers/verify-env.sh
.cursor/skills/verify-fence-pipeline/helpers/ensure-verify-db.sh
.cursor/skills/verify-fence-pipeline/helpers/cleanup.sh
source .cursor/skills/verify-fence-pipeline/helpers/verify-env.sh  # fresh EVDIR

fence-pipeline run \
  --receivables tests/fixtures/receivables.csv \
  --collections tests/fixtures/collections.csv \
  --output-dir "$EVDIR/out" \
  --database-url "$FENCE_VERIFY_DATABASE_URL" | tee "$EVDIR/cli-1.log"

.cursor/skills/verify-fence-pipeline/helpers/snapshot-db.sh
cp "$EVDIR/db-snapshot.txt" "$EVDIR/db-snapshot-1.txt"

fence-pipeline run \
  --receivables tests/fixtures/receivables.csv \
  --collections tests/fixtures/collections.csv \
  --output-dir "$EVDIR/out" \
  --database-url "$FENCE_VERIFY_DATABASE_URL" | tee "$EVDIR/cli-2.log"

.cursor/skills/verify-fence-pipeline/helpers/snapshot-db.sh
# payment_allocations / amount_applied counts must match snapshot 1
```

## Gotchas

- Orphan payments (receivable not in active book) raise an exception and stay
  pending — they are **not** marked `payments_applied`, so a later export that
  adds the receivable can allocate them. `payment_id` idempotency still applies
  once a payment is successfully allocated.
- Resume (`--resume`) re-enters from `current_stage`; allocation still skips
  applied `payment_id`s.
