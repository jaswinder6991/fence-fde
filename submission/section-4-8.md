
## 4. Eligibility Criteria

A receivable is eligible for inclusion in the borrowing base only if it satisfies all of the following criteria as at the applicable Calculation Date.
### 4.1 Starfleet representations

The following criteria are represented by Starfleet on each Portfolio Declaration and are not computed from the receivables feed:

1. **Agreement form.** The receivable arises under the agreed template Installment Sale Agreement and the applicable Purchase Agreement.

2. **Affiliate status.** Neither the Merchant nor the Obligor is an Affiliate of Starfleet.

**By including a receivable in its Portfolio Declaration, Starfleet represents that these criteria are satisfied for that receivable.**

### 4.2 Feed-based eligibility criteria

The following criteria are tested by Fence from the receivables feed:

|Criterion|Test|
|---|---|
|Face Value|Face Value is less than or equal to USD 100,000.|
|Currency|Currency is USD.|
|Obligor Score|Obligor Risk Score is greater than or equal to 65.|
|Eligible Sector|Merchant principal activity is in one of the following Eligible Sectors: Logistics, Manufacturing, Retail, or Hospitality.|
|Default status|A receivable is Defaulted if Days Past Due is greater than or equal to 90, including a written-off receivable with zero Outstanding Balance. A receivable with zero Outstanding Balance and Days Past Due below 90 is Closed, not Defaulted.|
|Same-obligor default|No other receivable owed by the same Obligor is Defaulted.|

For the purposes of the same-obligor default test, receivables are grouped by the Obligor identity supplied in the portfolio data.
The current feed identifies Obligors by name. Name-based matching may fail to identify the same Obligor consistently where names differ due to spelling variations, missing middle names or other inconsistencies. For the prototype, receivables will be grouped using a normalized Obligor name.

**Production requirement:** Starfleet should provide a stable, unique `obligor_id` for each Obligor, used consistently across all receivables and declarations. Operations must confirm the availability and uniqueness of this identifier with Starfleet before go-live.

A receivable that fails any eligibility criterion is excluded from the Eligible Receivables pool for that Calculation Date. The receivable remains part of the Portfolio.

Where a feed-based eligibility criterion cannot be evaluated because required data is missing or invalid, the receivable is treated as ineligible for the applicable Calculation Date and is excluded from the Eligible Receivables pool and the Aggregate Eligible Balance. Fence records the reason in the processing report and identifies the receivable and missing or invalid data so that Starfleet can investigate and correct the declaration promptly.

The receivable remains part of the Portfolio. Once corrected data is received, eligibility is reassessed at the next applicable Calculation Date.



## 8. Concentration Limits

At each Calculation Date, no more than 40% of the Aggregate Eligible Balance may relate to Eligible Receivables whose Merchants carry on their principal activity in any single Eligible Sector.

The Eligible Sectors are Logistics, Manufacturing, Retail and Hospitality.

For the purposes of this calculation, sector concentration is measured using the **Outstanding Balance** of Eligible Receivables, not their Face Value. The sector balance is the aggregate Outstanding Balance of all Eligible Receivables relating to Merchants in that sector. The denominator is the Aggregate Eligible Balance, being the aggregate Outstanding Balance of all Eligible Receivables.

A sector exceeds the Sector Concentration Limit where its sector balance is greater than 40% of the Aggregate Eligible Balance.

The amount by which a sector's balance exceeds the 40% limit is its Excess Concentration Amount. Excess Concentration Amounts for all sectors are deducted from the Aggregate Eligible Balance for purposes of calculating the Borrowing Base.

A concentration excess does not make the affected receivables ineligible and does not constitute an Event of Default. The excess continues to be deducted for so long as it subsists.

The Borrowing Base is calculated after applying any applicable Excess Concentration Amounts.

### Worked example — Sector concentration

Assume the Aggregate Eligible Balance is USD 100 million, comprising the following Eligible Receivables:

|Eligible Sector|Outstanding Balance|% of Aggregate Eligible Balance|
|---|--:|--:|
|Logistics|USD 20m|20%|
|Manufacturing|USD 15m|15%|
|Retail|USD 50m|50%|
|Hospitality|USD 15m|15%|
|**Total**|**USD 100m**|**100%**|

The Sector Concentration Limit is 40% of the Aggregate Eligible Balance. Retail exceeds this limit by USD 10 million.

The Excess Concentration Amount is therefore USD 10 million. This amount is deducted from the Aggregate Eligible Balance for purposes of calculating the Borrowing Base.

The adjusted balance is USD 90 million. Applying the 85% advance rate gives a Borrowing Base of USD 76.5 million.

The concentration excess does not make the Retail receivables ineligible and does not constitute an Event of Default.
