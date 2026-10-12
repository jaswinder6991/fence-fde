# Correction Flow — v1 Behavior

Starfleet submits a full portfolio snapshot for each declaration. The feed does not include a separate correction flag, so Fence compares the newly declared terms with those previously recorded for each receivable.

The v1 implementation distinguishes between ordinary updates to a receivable's current position and changes to terms used to build its expected payment schedule.

## Processing flow

![mermaid-diagram](https://hackmd.io/_uploads/SyjD0rtjGe.png)


## What happens when terms change?

- **New receivable:** Fence creates the receivable record and builds its expected payment schedule from the initially declared terms.
- **Existing receivable with no schedule-defining changes:** Fence updates the current record with the latest successfully parsed declaration, including Outstanding Balance and Days Past Due. The existing schedule is retained.
- **Existing receivable with schedule-defining changes:** Fence records a schedule-drift exception and retains the original schedule. The current receivable record is still updated from the latest successfully parsed declaration.
- **Pool calculation:** Fence evaluates status and eligibility using the latest declared Outstanding Balance and Days Past Due, along with the other facility criteria. AEB is calculated from the Outstanding Balances of Eligible Receivables.
- **Operational reporting:** The report identifies the affected receivable and makes the discrepancy visible. The schedule-drift exception does not, by itself, make an otherwise eligible receivable ineligible.

## Important limitation

In v1, detecting a change does not initiate a correction workflow or rebuild the expected payment schedule. The latest declared terms can therefore drive eligibility and the borrowing-base calculation while payment allocation continues against the original schedule.

This behavior is intentional in the prototype: Fence flags unsupported changes rather than silently inventing new schedule terms or remapping existing payment allocations. It is not a complete production correction workflow.

## Example

A receivable is initially declared with a face value of $10,000 and four installments. Fence builds four installments of $2,500 each.

A later snapshot declares:

- Face value: $8,000
- Number of installments: 4
- Purchase date: unchanged
- Outstanding Balance: $8,000
- Days Past Due: 0

Fence flags the changed face value and retains the original four $2,500 installments. It uses the latest declaration for status, eligibility and the declared-balance pool calculation. If the receivable meets the other eligibility criteria, it remains Current and eligible, and its $8,000 Outstanding Balance contributes to AEB.

The original schedule and latest declared position therefore remain different until the discrepancy is resolved. Operations can see the exception, but v1 does not automatically reconcile the two.

## Production requirement

Before correction support is introduced in production, Fence and Starfleet must agree how corrected terms affect:

- Expected installment amounts and due dates
- Payments already allocated to the original schedule
- Reconciliation between the declared balance and the platform-calculated position


Until those rules are agreed, retaining the original schedule and reporting the discrepancy is a limitation of v1, not a complete correction solution.