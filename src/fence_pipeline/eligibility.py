"""Data-backed eligibility criteria (criteria 1 and 7 deferred)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fence_pipeline.parse import is_eligible_sector
from fence_pipeline.status import STATUS_DEFAULTED, derive_status

MAX_FACE_VALUE = Decimal("100000")
MIN_OBLIGOR_SCORE = 65


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    status: str
    reasons: list[str]


def evaluate_eligibility(
    *,
    face_value: Decimal,
    obligor_risk_score: int,
    sector: str,
    currency: str,
    outstanding_balance: Decimal,
    days_past_due: int,
    obligor_has_other_defaulted: bool,
) -> EligibilityDecision:
    status = derive_status(outstanding_balance, days_past_due)
    reasons: list[str] = []

    if face_value > MAX_FACE_VALUE:
        reasons.append(f"face_value exceeds {MAX_FACE_VALUE}")
    if obligor_risk_score < MIN_OBLIGOR_SCORE:
        reasons.append(f"obligor_risk_score below {MIN_OBLIGOR_SCORE}")
    if not is_eligible_sector(sector):
        reasons.append(f"sector not eligible: {sector}")
    if currency.upper() != "USD":
        reasons.append(f"currency not USD: {currency}")
    if status == STATUS_DEFAULTED:
        reasons.append("receivable is Defaulted")
    if obligor_has_other_defaulted:
        reasons.append("other receivable for same obligor is Defaulted")

    return EligibilityDecision(
        eligible=len(reasons) == 0,
        status=status,
        reasons=reasons,
    )
