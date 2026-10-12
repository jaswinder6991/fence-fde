## 5. Expected payments

An expected payment is a scheduled amount a receivable owes on a given date. The platform generates the full schedule for each receivable at declaration; Starfleet does not supply it.

- **Method: equal installments.** The `face_value` is repaid in `number_of_installments` equal amounts. Each installment = `face_value / number_of_installments`.

- **Frequency and dates:** one installment per month, on the same day-of-month as `purchase_date`, the first falling one month after purchase.

- **No interest split.** A receivable installment has no interest-versus-principal breakdown. Each installment is simply a fixed repayment toward the face value. Starfleet's return comes from the purchase discount, not from interest.

### Example

A receivable with `face_value = $10,000` and
`number_of_installments = 4` generates 4 monthly expected payments of
$2,500 each.


## 5.1 Corrections after purchase

Section 3.1 identifies **face_value**, **purchase_date** and **number_of_installments** as immutable after the initial declaration. However, subsequent full snapshots may contain different values, and the feed does not include a separate correction record.

In v1, Fence detects changes to these schedule-defining fields, records a schedule-drift exception and retains the schedule created from the initial declaration. It does not attempt to infer the intended correction or remap payments already allocated to the original schedule.

Face value changes. A lower declared face_value with the same purchase_date and number_of_installments may represent a partial cancellation or return. The commercial interpretation and any required schedule treatment should be confirmed with Starfleet. In v1, the original installment amounts and due dates remain unchanged, while the current receivable record is updated with the latest declared values.

Purchase date or installment-count changes. A subsequent change to either field is also flagged. Because the resulting schedule and treatment of prior allocations have not been fully specified, v1 retains the original schedule rather than inventing new due dates or installment amounts.

Pool calculation. Status and eligibility use the latest successfully declared outstanding_balance and days_past_due, together with the other facility criteria. A schedule-drift exception does not automatically make a receivable ineligible. An otherwise eligible receivable contributes its latest declared Outstanding Balance to Aggregate Eligible Balance (AEB), even when that balance does not reconcile to the retained schedule.

### Example. 
A receivable is initially declared with a face value of $10,000 and four installments, producing four installments of $2,500. A later snapshot declares a face value of $8,000, the same purchase date and installment count, an Outstanding Balance of $8,000, and Days Past Due of zero.

In v1, Fence flags the changed face value and retains the original four $2,500 installments. The latest declaration is used for status, eligibility and the declared-balance pool calculation. If the receivable meets the other eligibility criteria, it remains Current and eligible, and its $8,000 Outstanding Balance contributes to AEB. The discrepancy between the latest declared terms and the retained schedule remains visible for Operations to investigate.

Production requirement. Before supporting corrections in production, Fence and Starfleet must agree how corrected terms affect the expected schedule, existing payment allocations, reconciliation and the effective Calculation Date. Until those rules are defined, flagging and retaining the original schedule is a limitation of v1, not a complete correction solution.