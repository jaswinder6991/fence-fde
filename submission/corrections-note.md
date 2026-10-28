# Corrections flow — implementation note

## How history is kept

Each live receivable has one pointer: `current_schedule_version` on
`receivables_current`. Installment rows and payment allocations carry the same
`schedule_version`. Allocate and reports use only the version the pointer names;
older versions stay in the database for audit.

On a term change (`face_value` or `number_of_installments` so the Section 5 plan
no longer matches the current version):

1. Leave the old version’s installments and allocations untouched (audit).
2. Insert a new version built from the latest terms.
3. Point `current_schedule_version` at that new version.
4. Clear `payments_applied` for that receivable’s payments so allocate re-applies
   cash onto the **current** plan only (same `payment_id`s — nothing double-counted).
5. Record `source=correction` with before→after face / installment count and version numbers.

### Example (SL-C-1 after export-2)

Face fell from 6000 → 3000; installment count stayed 6. Two $1000 payments already
existed on the old plan.

**`receivables_current`**

| receivable_external_id | face_value | current_schedule_version |
|------------------------|------------|--------------------------|
| SL-C-1                 | 3000       | **2**                    |

**`installments`** (both versions kept)

| receivable | schedule_version | seq | amount_due | role        |
|------------|------------------|-----|------------|-------------|
| SL-C-1     | 1                | 1–6 | 1000 each  | audit only  |
| SL-C-1     | **2**            | 1–6 | 500 each   | **live**    |

**`payment_allocations`**

| payment_id | schedule_version | installment_seq | amount | role       |
|------------|------------------|-----------------|--------|------------|
| P-C1-1, …  | 1                | …               | …      | audit only |
| P-C1-1, …  | **2**            | …               | …      | **live**   |

Live work always joins `schedule_version = current_schedule_version` (here, 2).

### Why this shape

We needed the old plan and its allocations to stay queryable, and the live path to
keep using one clear “current” schedule. Putting a version on installment and
allocation rows, plus a single pointer on the live receivable, does that without
a second copy of the book.

Other shapes were weaker for us:

- **`is_current` on every installment row** — redundant, easy to leave two
  versions “current.”
- **`MAX(version)` with no pointer** — no explicit live plan; awkward if a bad
  rebuild must not become current.
- **Archive / snapshot tables** — history exists, but audit uses a different
  shape than the hot path.
- **Version the whole receivable** — duplicates `receivable_events`; only the
  derived schedule needed versioning.

---

## Cases the PDF leaves open

The must-hold bullets say: new schedule, conserve payments, keep history, stay
idempotent, report corrections. They do not say what to do when cash already sat
on the old plan, or when that cash exceeds the new face. Those show up in the
corrections fixture.

### 1. Payments already collected on the older schedule

**In the data:** four of five corrected IDs already had collections before export-2
(SL-C-1, SL-C-3, SL-C-4, SL-C-6). Only SL-C-5 had none.

**Platform:** rebuild the schedule, then re-apply every known `payment_id` onto the
new plan with the same §7.3 waterfall used for ordinary collections (earliest
unpaid first; excess rolls forward within the plan).

| | |
|--|--|
| **Status / eligibility** | Still from Starfleet’s declared outstanding balance and DPD. |
| **Operations** | Sees `source=correction` (what changed) and the new schedule; old version remains queryable. |
| **Lender figures** | AEB / borrowing base still use **declared** outstanding — not “face − applied”. |

### 2. Closed receivable with excess after correction

**In the data:** SL-C-3 (paid 1000 vs new face 800) and SL-C-6 (paid 4000 vs new
face 3000). Both declare outstanding balance **0** after the correction.

**Platform:** fill the new schedule under §7.3; any cash left after the plan is
full is an allocate **excess** exception. We do not invent further installments
or change declared OB.

| | |
|--|--|
| **Status** | Closed when declared OB is 0 (and DPD &lt; 90). |
| **Operations** | Sees excess amount and receivable id; refund / credit is Starfleet’s call. |
| **Lender figures** | Receivable contributes **0** to AEB via declared OB. Excess is **not** written into the borrowing base. |

### 3. `purchase_date` change

**In the corrections fixture:** never happens. The original facility materials
treat purchase date as schedule-defining; this follow-up feed does not exercise it.

**Platform:** if due dates ever diverged from the current plan, we would rebuild
the same way as a face / N change. Not demonstrated here.

---

## Question for Operations

When the platform shows excess cash after the current schedule is full (paid more
than the corrected face — a credit / “negative owe” vs face while Starfleet may
still declare OB = 0):

**Should the lender see that excess**, or should it stay an Ops ↔ Starfleet
exception (report only), with the lender continuing to rely on declared
outstanding alone?

---

## Results — original full exports

| Export | Corrected | AEB | Borrowing base |
|--------|----------:|----:|---------------:|
| `receivables.csv` + `collections.csv` | 0 | 179,481,301.98 | 152,559,106.68 |
| `receivables-later.csv` + `collections-later.csv` | **19** | 147,586,696.14 | 123,269,981.43 |

AEB / BB match pre-corrections runs on the same files (pool uses declared OB).

Corrections fixture: **5** corrected (SL-C-1,3,4,5,6); SL-C-2 position-only;
SL-C-3 / SL-C-6 excess after the new plan is filled.
