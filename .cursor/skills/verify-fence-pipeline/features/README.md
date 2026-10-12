# Feature map — fence-pipeline

Index of user-facing CLI behaviors to verify. Drive each feature through
`fence-pipeline` against `fence_verify` and capture evidence under
`../evidence/`. Prefer
[`helpers/drive-pipeline-run.sh`](../helpers/drive-pipeline-run.sh) for the
happy path.

| Feature | File | Primary proof |
|---------|------|----------------|
| Pipeline run (Ops happy path) | [pipeline-run.md](pipeline-run.md) | Three report CSVs + `run_id=` |
| Payment schedule settlement | [payment-schedule-settlement.md](payment-schedule-settlement.md) | `payment_id` / `amount_from_payment` on schedule |
| Receivables position (as-of export) | [receivables-position.md](receivables-position.md) | Status / outstanding / eligibility per receivable |
| Pool borrowing base | [pool-borrowing-base.md](pool-borrowing-base.md) | AEB, concentration, BB in pool report |
| Soft-fail and payment idempotency | [soft-fail-and-idempotency.md](soft-fail-and-idempotency.md) | Exceptions + re-run does not double-apply |

A proof that only exercises one entry point is incomplete when another feature
in this map is in scope for the change under review.
