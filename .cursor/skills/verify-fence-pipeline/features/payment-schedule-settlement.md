# Payment schedule settlement

Expected installment schedule after collections allocation, including which
`payment_id` hit each installment.

## Sub-features

- Equal installments generated from face value / count
- Earliest-unpaid waterfall allocation
- Schedule report rows with `payment_id` and `amount_from_payment`
- Unpaid installments present with empty `payment_id`

## How to get to it (user POV)

Run the pipeline; open
`payment_schedule_report_run_<id>.csv` in the output directory.

## Driving it with the CLI harness

```bash
.cursor/skills/verify-fence-pipeline/helpers/drive-pipeline-run.sh
# Inspect $EVDIR/out/payment_schedule_report_run_*.csv
```

Fixture expect:

- Installments 1–2 settled by `P-TEST-1` and `P-TEST-2` at 2500.00 each
- Installments 3–4 unpaid (`payment_id` blank)

## Gotchas

- One schedule row per **allocation**; multiple payments on one installment
  yield multiple rows sharing `installment_seq`.
- Status / outstanding do **not** belong in this file (see receivables-position).
