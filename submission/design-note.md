## 1. Design philosophy

The central design decision is to separate **what Starfleet sends us from what Fence derives from it**.

Receivables and collections are ingested into persistent event records before the pipeline derives its current view of the portfolio. This creates an audit trail of received data and helps investigate disagreements about what Starfleet submitted. The current implementation retains parsed event data and run metadata, rather than a byte-for-byte archive of the original CSV files; preserving raw source files would be a useful production improvement.

From these inputs, the pipeline builds and maintains distinct layers:

1. **Receivable events** record receivable rows received in each run.
2. **Payment events** record payments by `payment_id`, preventing the same payment from being ingested repeatedly.
3. **Current receivables** represent the latest successfully parsed declaration for each receivable.
4. **Expected payment schedules** represent installments derived from receivable terms.
5. **Payment allocations** record how reported collections are applied to installments.
6. **Reconciliation and exception results** identify discrepancies between declared data and Fence's derived position.
7. **Eligibility, concentration and borrowing-base results** apply the facility rules and preserve the result for each run.

These layers have different responsibilities. An incoming value, a derived value and a contractual conclusion are not interchangeable. Keeping them separate makes the calculation explainable and makes it possible to investigate where a result came from.

PostgreSQL is therefore part of the processing design, not simply a place to store rows. The system must preserve state between full portfolio snapshots, distinguish previously processed payments from new ones, compare successive results and recover from interrupted processing. The prototype persists run state and supports stage-level resumption, while idempotent payment processing protects against duplicate allocations.

A second principle is that **data quality is part of the integration responsibility**. Starfleet provides the declaration, but Fence must validate what actually arrives. If a required field is missing, a payment cannot be matched, a declared balance does not reconcile, or a receivable's terms change unexpectedly, the system must make that discrepancy visible.

Some exceptions affect eligibility and exclude a receivable from the eligible pool for that Calculation Date. Others, such as an outstanding-balance mismatch, may not change eligibility but still require investigation. Fence should not silently repair the client's data or treat every discrepancy as a reason to reject the entire portfolio. It should produce a defensible result, make exceptions visible and give Operations enough context to decide what needs follow-up.

## 2. How the pieces fit together

### 2.1 Ingestion and event history

The prototype processes full receivables snapshots of approximately 50,000 rows and full collections histories of approximately 230,000 rows.

Each receivables export describes the portfolio as declared at that point in time. It contains existing receivables, potentially with updated outstanding balances or days past due, as well as new receivables. Receivables absent from a later full snapshot are marked inactive in the current book.

Collections exports contain the full payment history. A payment may therefore appear in many exports, but it must only be ingested and applied once.

The receivable event history records parsed rows with run ID, ingestion time and source row number. Parsing failures are also recorded with an error and any receivable ID that can be identified. The original filename is available through the run record, but the raw CSV row itself is not stored in the event table.

For collections, payment events are deduplicated by `payment_id`. A resent payment is skipped rather than stored as a second payment event.

This is sufficient for the prototype's processing and investigation workflow, but it is important to be precise about the guarantee: the system retains parsed event history, not a complete raw-file archive.&#x20;

### 2.2 Current receivables

`receivables_current` is the working view of the latest successfully parsed declaration, with one current record per `receivable_external_id`.

- New receivables are inserted.
- Existing receivables are updated from the latest successfully parsed event, including declared outstanding balance and days past due.
- Receivables absent from the full snapshot are marked inactive.
- Event history, schedules, allocations and per-run results are retained separately.

The `active` flag indicates whether a receivable appears in the latest declared portfolio. It is distinct from the contractual status of Current, Delinquent, Defaulted or Closed.

This design avoids cloning the entire portfolio for each run while preserving the results needed to compare declarations. The live book reflects the latest input, while eligibility and pool results are recorded against a particular run.

### 2.3 Expected payment schedules

Fence generates an expected installment schedule from the receivable terms. Installments are equal, with no interest split; rounding remainder is assigned to the final installment. The first installment is due one month after the purchase date, with subsequent installments on the same day of the month.&#x20;

Schedules provide the expected installment position against which collections can be allocated and reconciled. They are not the same thing as the latest declared outstanding balance.

This distinction matters when Starfleet restates terms after purchase. In v1, the pipeline detects schedule drift and retains the existing installment rows instead of silently rebuilding them. However, the live receivables record is still updated with the latest declared attributes, including fields described as immutable in the facility terms. Consequently, eligibility can use the latest declared face value while allocations continue against the prior schedule.

That behavior is a known prototype limitation. It should be made visible in the exception report and resolved through an agreed correction workflow before production use.

### 2.4 Collections and payment allocation

The pipeline applies new payments to the expected schedule using the supplied `payment_id` as the idempotency key.

For each pending payment, it validates and matches the row, allocates the amount to the earliest unpaid installment, and carries excess forward according to the stated allocation rule. The resulting allocations, installment applied amounts and payment-applied marker are committed together in batches.

The applied marker means the payment was successfully allocated, or intentionally closed after a real apply attempt against an active receivable (for example overpayment leftover or no schedule). An orphan payment, where the receivable is not in the active book, is not marked applied. It stays pending so a later run can allocate it if the receivable becomes active.

The allocation stage processes only payments without an applied marker. On entry to the stage, it also cleans up partial allocation records that lack the corresponding applied marker and recomputes affected installment totals.

This protects against interrupted batches:

- If a batch fails before commit, its transactional changes are rolled back.
- If a batch commits, the applied marker prevents those payments from being allocated again.
- If partial allocation rows exist without an applied marker, stage cleanup removes them before the stage is rerun.

The protection is designed around sequential execution. The prototype's single-writer check looks for other runs marked `running`; it is not a PostgreSQL lock and does not prevent two processes that bypass the check from racing. Production should enforce mutual exclusion at the database or orchestration level.

### 2.5 Reconciliation and exceptions

Fence calculates a platform outstanding position from face value and allocated collections, then compares it with Starfleet's declared outstanding balance.

The declared balance and Fence's calculated position have different roles. The prototype uses Starfleet's declared outstanding balance and days past due for the contractual pool calculation, while the independently calculated position is a reconciliation check. A mismatch is reported rather than silently resolved by substituting one amount for the other.

The pipeline handles malformed rows, missing required fields, payment parsing failures, orphan payments, schedule drift, overpayment leftovers and outstanding-balance mismatches.

The response depends on the exception:

- **Invalid required receivable data:** the row is recorded as an exception and does not enter the current active book or AEB calculation.
- **Contractual ineligibility:** the receivable remains in the portfolio but is excluded from the eligible pool for that Calculation Date.
- **Orphan payment:** the receivable is not in the active book. The payment is reported as an exception and left pending (not written to `payments_applied`) so later runs can allocate it if the ID becomes active. Ops should ask Starfleet for the missing receivable or to drop a bad `payment_id`. Forever-junk IDs retry each night until Starfleet stops sending them or an expire/ignore policy is added.
- **Outstanding-balance mismatch or schedule drift:** the exception is reported but does not automatically make an otherwise eligible receivable ineligible.
- **Processing failure:** a run that cannot produce a trustworthy result must be distinguished from a run that completed with row-level exceptions.

This is deliberate soft-fail behavior. A bad row should not necessarily prevent useful processing of the remaining portfolio, but the system must not hide the problem or imply that the affected data was reconciled.


### 2.6 Status, eligibility and borrowing base

The status rules need to distinguish a receivable that has been repaid from one that has been written off. David clarified that a zero-balance receivable with at least 90 days past due is **Defaulted**, not Closed. The prototype now applies the rules in this order:

1. **Defaulted** if days past due is at least 90, including zero-balance written-off receivables.
2. **Closed** if outstanding balance is zero and days past due is below 90.
3. **Delinquent** if days past due is at least 30.
4. **Current** otherwise.

This ordering ensures that a zero-balance receivable that meets the default threshold is not incorrectly treated as repaid. Defaulted receivables are ineligible. Delinquent receivables are not automatically excluded solely because they are delinquent.

The eligibility checks include face value no greater than $100,000, obligor risk score of at least 65, USD currency, an eligible sector, and the requirement that neither the receivable nor another receivable owed by the same Obligor is Defaulted. The eligible sectors are logistics, manufacturing, retail and hospitality.

The agreement-form and Affiliate requirements are representations made by Starfleet with each declaration, rather than checks against fields in the supplied CSVs. The prototype does not claim to verify these independently.

For the same-obligor rule, the prototype uses normalized obligor names because a stable unique ID is not supplied. Production should use a stable `obligor_id` agreed with Starfleet.

The pool calculation is:

1. Sum the outstanding balances of Eligible Receivables to calculate Aggregate Eligible Balance (AEB).
2. Calculate eligible outstanding balance by sector.
3. Calculate each sector's excess above 40% of AEB.
4. Deduct the total excess from AEB.
5. Apply the 85% advance rate.

`AEB = sum(outstanding_balance of Eligible Receivables)`

`Sector Excess = max(0, eligible sector outstanding − 40% × AEB)`

`Adjusted Eligible Balance = AEB − sum(Sector Excess)`

`Borrowing Base = Adjusted Eligible Balance × 85%`

David confirmed that the concentration cap is measured over Eligible Receivables using outstanding balance. The excess is deducted from AEB; it is not itself an Event of Default and does not make the underlying receivables ineligible.

The prototype reports the computed borrowing base and separately shows headroom against the $250 million facility limit. Whether the displayed borrowing-base figure itself should be capped at that limit remains to be confirmed. 


### 2.7 Operational reporting

The output is designed for Fence Operations. The purpose of the HTML summary is to let Ops determine whether a run completed, understand the financial result, identify what changed from the previous run and see what needs investigation.

Detailed CSVs provide receivable positions, payment schedules and row-level exceptions. These allow Ops to investigate individual discrepancies and, where appropriate, send a correction request to Starfleet.

The report should make it possible to trace the borrowing-base calculation through eligible receivables, sector balances, the concentration deduction and the advance rate. It should also distinguish a completed run with exceptions from a failed or incomplete run. The report is an operational control, not a substitute for resolving exceptions or for the agreed facility reporting process.

## 3. What I decided, what I decided against, and why

### Append-only receivable history instead of diff-only logging

I chose to record receivable rows by run rather than keep only the differences between snapshots. Diff-only logging saves storage but makes disputes and historical investigation harder. A new run containing the same receivables still records a new set of receivable events; resuming ingestion for the same run clears that run's prior events and re-ingests them, avoiding duplicate copies within the run.

Payment events are different: they are inserted once by `payment_id`, since the same payment is expected to recur in later full-history exports.

### PostgreSQL and persistent state instead of in-memory processing

The second receivables snapshot must be reconciled with the existing book, and collection history must be deduplicated across runs. Allocations, schedules, exceptions and run results must survive process restarts. PostgreSQL provides the persistent state and transactional writes required for this workflow, as well as set-based queries that can be optimized as volume grows.

### Stage-level resume instead of restarting the whole pipeline

The prototype persists `current_stage` for each run. When resumed, it restarts at the beginning of that stage and reruns every subsequent stage. It does not resume at an individual row or payment within a stage.

This is an intentional recovery design: each stage must be safe to rerun through cleanup, upserts, deterministic derived results and idempotency. It is more robust than requiring the operator to start over blindly, but it is not a claim of fine-grained checkpointing.

### Idempotent payment allocation instead of replaying all payments

The collections file is a full history, so treating every row in each export as a new payment would overstate repayments. `payment_id`, persisted applied markers and cleanup of partial allocations prevent a retried stage from applying the same payment twice during sequential execution.

### Soft-fail row processing instead of aborting on every bad row

I chose to record row-level exceptions and continue where a trustworthy result can still be produced. Invalid required receivable rows are excluded from the current book and AEB inputs. Reconciliation mismatches and schedule drift are reported without automatically changing eligibility. This gives Ops useful results while making the limitations visible.

### Declared outstanding balance for the pool calculation

I chose not to replace Starfleet's declared outstanding balance with Fence's calculated amount. The declared value is the contractual input used by the prototype, while Fence's independent calculation is a reconciliation check. Where the values disagree, Ops receives an exception to investigate rather than a silent correction made by the integration.

### Flag unsupported corrections instead of inventing semantics

The prototype detects changes to schedule-defining fields and retains the existing schedule, but updates the live receivable with the latest successfully parsed attributes. This means the current face value can affect eligibility even while the prior schedule remains in use. It is a limitation that must be addressed before production, not a complete correction solution.

## 4. What I left out and why

**Full correction and schedule-rebuild workflow.** Rebuilding a schedule can change installment amounts and due dates, but the treatment of payments already allocated to the old schedule has not been fully specified. I chose to flag drift rather than invent a remapping rule. The prototype's current behavior is deliberately limited and should not be mistaken for full correction support.

**Raw-file archival and exact historical replay.** The event tables preserve parsed row data and run metadata, but not the raw CSV line. Reprocessing the same inputs can reconstruct a calculation only if the source data, relevant declaration, processing rules and their versions are also preserved. Production should archive original inputs and define how a Calculation Date maps to declarations and rule versions before promising exact historical replay.

**Concurrency beyond the single-writer prototype.** The current single-writer check is cooperative and is not a database lock. Production should enforce exclusive processing through a database lock or orchestration mechanism before allowing multiple workers or concurrent runs.

**Final policy for one borrowing base per day versus each declaration.** The platform supports repeated runs, but the official Calculation Date and lender-reporting policy must be agreed operationally.

**A stable obligor identity model.** Normalized names are an interim key. A stable `obligor_id` should be part of the production feed contract.

**A definitive facility-limit presentation.** The prototype reports the calculated borrowing base and headroom against the $250 million limit separately. The final presentation rule still needs confirmation.

These omissions separate implementation choices from unresolved contractual and operational decisions. They avoid making the code appear more definitive than the source data and agreement allow.

## 5. What would I hand to Operations so they could run this deal without me?

I would hand over the CLI and environment setup, a runbook, expected outputs, a recovery procedure and a clear exception escalation path.

The documented workflow is to run the pipeline against the receivables and collections files, inspect the resulting HTML pool summary and CSV details, and record the run ID. If a run fails, Ops can resume it using that ID. The runbook must state that resumption restarts the current stage and later stages, rather than continuing from a row-level checkpoint.

The morning checklist should cover:

1. Confirm that the latest run completed and produced its reports.
2. Review changes in AEB, sector concentration, borrowing base and exception counts relative to the previous run.
3. Investigate balance mismatches, orphan payments, invalid receivable rows and schedule-drift exceptions.
4. Confirm there is no stale run left marked `running` that blocks the next run.
5. Route data issues to Starfleet and unresolved contractual interpretations to the appropriate owner.
6. Verify that any correction appears in a later declaration and that its impact is visible in the next run.

The handover is complete when the team can run the pipeline, assess whether the result is usable, investigate exceptions and escalate decisions without relying on the original implementer.

## 6. What belongs in the platform, and what belongs in this deal?

The platform should provide reusable capabilities: event ingestion, full-snapshot reconciliation, current-book projection, schedule generation, idempotent allocation, exception handling, resumable stages, run results and reporting.

The deal should configure the eligible sectors, $100,000 face-value limit, minimum score of 65, USD requirement, status and same-obligor rules, 40% concentration cap, 85% advance rate and $250 million facility limit. The agreement-form and Affiliate requirements remain declaration representations.

A versioned configuration and rule set would make the calculation more reusable and help explain which policy produced a particular result.

## 7. What would I take to the Product team?

**A first-class exception and correction workflow.** The platform should support detecting a problem, explaining it to the client, receiving corrected data, reprocessing it and retaining the history of the original and corrected declarations.

**Raw input retention and replayability.** Persisting parsed events is useful, but production auditability would be stronger with immutable copies of source files, source checksums and explicit declaration and rule versions.

**A standard full-snapshot model.** The platform should standardize how new, updated and missing records are reconciled, while distinguishing inactivity from contractual closure and ineligibility.

**Reconciliation as an operational experience.** Ops should see the declared balance alongside Fence's calculated position, the size of the difference and the action required. Exceptions should be actionable rather than just logged.

**Versioned facility rules and operational run states.** Eligibility, concentration and advance rates should be configurable and versioned. Run status should distinguish completion with exceptions from a failed run, show the stage and recovery status, and make clear whether the financial result is safe to use.

**Safer concurrency and scalable processing.** A production platform should enforce mutual exclusion where stateful allocation requires it, and provide bulk-ingestion and set-based allocation patterns that work across deals.

## 8. What did running at this scale change, and what would I do differently at 10×?

At approximately 50,000 receivables and 230,000 collection rows, the workload was large enough to expose data-quality and query-performance issues.

During the second export, allocation took around 15–20 minutes for approximately 13,000 new payments. In an earlier version of the prototype, the cleanup query used `payment_id NOT IN (SELECT …)` against around 230,000 applied IDs. Replacing that pattern with `NOT EXISTS` addressed the bottleneck by allowing PostgreSQL to use a more suitable anti-join plan.

I see this as a useful performance lesson from testing the prototype against a realistic data volume. The issue was not that `NOT IN` is always inappropriate for small ingests; it was that this particular query pattern became expensive at the observed scale. Reviewing the query plan and measuring the stage helped identify a targeted fix rather than requiring a broader redesign.

The exercise also showed why persistent state and idempotency matter: the second export overlaps heavily with the first, so the system must distinguish new information from repeated information. Missing required fields and orphan payments also demonstrated why the pipeline needs row-level exceptions rather than assuming clean data.

At ten times the volume, I would profile first and then prioritize bulk ingestion with PostgreSQL `COPY`, set-based allocation and aggregation, batched writes, and avoiding unnecessary ORM loading of the full installment book. I would evaluate partitioning or archiving event history based on retention and query patterns, and only parallelize independent stages once shared-state writes and allocation are protected against races.

I would also add large-file performance tests, idempotency and crash-recovery tests, and sampled reconciliation checks to CI. I would not introduce distributed processing solely because the dataset is ten times larger; I would first measure which stages are CPU-bound, I/O-bound or query-bound.

The invariant remains the same at every scale: **a payment must never be applied twice, and the financial result must remain explainable.**


## 9. Would I have sequenced the three parts differently?

I would distinguish submission order from build order. For the build, I would:

1. Define the eligibility, concentration and borrowing-base rules.
2. Build the normal path from ingestion through current state, schedules, payment allocation, eligibility and reporting.
3. Specify and implement the full correction workflow once the consequences of restating terms and remapping prior payments are agreed.

This order prioritizes correctness of the normal financial path before introducing a correction workflow whose contractual behavior is not fully specified. I would still include a thin correction hook in the normal path from the beginning: detect unsupported changes, record an exception, retain the prior schedule and make the borrowing-base impact visible.

The principle is to build the core path first, make unsupported changes visible from the start, and implement correction semantics only when the operational and contractual rules are clear.

## 10. Decisions to close before production

| Decision                           | Current position                                        | Next step                                            |
| ---------------------------------- | ------------------------------------------------------- | ---------------------------------------------------- |
| Stable obligor identity            | Normalized name used in prototype                       | Agree a stable `obligor_id` with Starfleet           |
| Facility limit presentation        | Calculated borrowing base and headroom shown separately | Confirm whether the primary figure is capped         |
| Calculation Date and resubmissions | Results keyed by run                                    | Define official daily and intraday reporting policy  |
| Corrections after purchase         | Drift flagged; prior schedule retained                  | Agree schedule rebuild and payment-remapping rules   |
| Raw source retention               | Parsed event history retained; raw row not stored       | Define source-file archive and retention policy      |
| Concurrent execution               | Cooperative single-writer check                         | Add enforced mutual exclusion for production         |
| Reconciliation exceptions          | Reported; declared outstanding drives pool calculation  | Define operational escalation and resolution process |

These items are a mix of source-data requirements, operational policy and technical hardening. They should be assigned to their owners and resolved explicitly rather than left as implicit assumptions in the implementation.

## Conclusion

The design separates incoming evidence from derived state and financial conclusions. Event history preserves what was received; current receivables reflect the latest declaration; schedules and allocations represent expected and applied payments; reconciliation exposes discrepancies; and eligibility, concentration and the advance rate produce the borrowing-base result.

The prototype prioritizes durable state, stage-level recovery, idempotent payment allocation and visible exceptions. It deliberately flags unsupported corrections rather than inventing a financial rule. The next step is to harden the audit and concurrency guarantees, close the remaining feed and facility decisions, and optimize measured bottlenecks. The goal is a system that Fence Operations can run without the original implementer and whose output is explainable and defensible.