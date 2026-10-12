# Receivables position (as-of export)

Per-receivable outstanding, status, and eligibility as of the Calculation Date
(the export snapshot), not per installment due date.

## Sub-features

- Export vs computed outstanding + mismatch flag
- Status: Closed / Defaulted / Delinquent / Current
- Eligibility reasons when ineligible
- One row per receivable

## How to get to it (user POV)

Run the pipeline; open
`receivables_position_report_run_<id>.csv`.

## Driving it with the CLI harness

```bash
.cursor/skills/verify-fence-pipeline/helpers/drive-pipeline-run.sh
# Inspect $EVDIR/out/receivables_position_report_run_*.csv
```

Fixture expect for `SL-TEST-1`:

- `export_outstanding=5000.00`, status `Current`, eligible true
- No `installment_seq` column

## Gotchas

- Closed fully-paid receivables show outstanding 0 on **every** sense of
  “current export,” not historical installment-time balances.
- Criteria 1 and 7 (form / affiliation) are intentionally omitted in v1.
