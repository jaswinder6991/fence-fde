# Corrections flow — implementation note

## How history is kept

Each receivable has a persistent pointer `receivables_current.current_schedule_version`.
Installments and payment allocations are keyed by `(receivable, schedule_version, …)`.

On a term change (`face_value` / `number_of_installments` such that the Section 5
plan no longer matches the current version):

1. Leave the old version’s installments and allocations untouched (audit).
2. Insert a new version built from the latest terms.
3. Point `current_schedule_version` at that new version.
4. Clear `payments_applied` for that receivable’s payments so allocate re-applies
   cash onto the **current** plan only (same `payment_id`s — nothing double-counted).
5. Record `source=correction` with before→after face/N and version numbers.

Allocate, evaluate, and the schedule report join only the current version.
Crash cleanup of partial allocations is also scoped to the current version so
reopening payments cannot wipe prior-version audit rows.

Optional later: a `receivable_schedules` header storing terms-at-version
([issue #1](https://github.com/jaswinder6991/fence-fde/issues/1)).

## Decisions beyond the PDF bullets

| Case | Decision |
|------|----------|
| Position-only OB/DPD update | Not a correction; keep schedule (existing path). |
| Excess cash after refill (paid > new face) | Waterfall fills through the **last** installment; leftover is an allocate exception (excess). Closed vs open comes from declared OB/DPD. AEB still uses declared OB — excess does not rewrite the pool. |
| Refund / credit of excess | Out of scope; Ops/Starfleet decision. We report it. |
| `purchase_date` change | Not in the fixture; any due-date mismatch vs current plan still rebuilds (same path as face/N). |
| Face/N increase | Same rebuild path; not seen in sample data. |
| Purchase price | Tracks Starfleet’s discount; not a schedule trigger. |

## Left out (and why)

- No separate `overpaid` money column — excess is visible in exceptions and in
  `amount_applied` vs `amount_due` on the current plan; a column can wait for Ops needs.
- No payment restatement handling — collections remain append-only by `payment_id`.
- No daytime mid-file correction API — still nightly full-export CLI.

## Questions for Operations

1. Should excess after a closed correction auto-suggest refund, or only alert?
2. If Starfleet later raises face again, is a third schedule version always correct?
3. Do you want term-at-version headers in the DB for reporting, or are events + installment rows enough?

## Results — original full exports (same DB, in order)

| Export | Corrected | AEB | Borrowing base |
|--------|----------:|----:|---------------:|
| `data/receivables.csv` + `collections.csv` | 0 | 179,481,301.98 | 152,559,106.68 |
| `data/receivables-later.csv` + `collections-later.csv` | **19** | 147,586,696.14 | 123,269,981.43 |

AEB / borrowing base match prior (pre-corrections-feature) runs on the same files.
That is expected: the pool still uses **declared** outstanding balance; rebuilding
schedules changes allocation/reconcile, not the contractual AEB inputs.

Corrections fixture (`data/corrections/`): export-2 marks **5** receivables corrected
(SL-C-1,3,4,5,6); SL-C-2 stays version 1. SL-C-3 / SL-C-6 report excess after the
new plan is filled (declared Closed / OB=0).
