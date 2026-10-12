"""Receivable status derivation from export fields."""

from __future__ import annotations

from decimal import Decimal

STATUS_CLOSED = "Closed"
STATUS_DEFAULTED = "Defaulted"
STATUS_DELINQUENT = "Delinquent"
STATUS_CURRENT = "Current"


def derive_status(outstanding_balance: Decimal, days_past_due: int) -> str:
    """Evaluate Defaulted → Closed → Delinquent → Current.

    Zero balance alone is not enough for Closed: a written-off receivable can
    sit at zero with high DPD. Until the export has a write-off flag, treat
    zero balance with ``days_past_due >= 90`` as Defaulted (a repaid book does
    not sit at ninety days late).
    """
    if days_past_due >= 90:
        return STATUS_DEFAULTED
    if outstanding_balance == 0:
        return STATUS_CLOSED
    if days_past_due >= 30:
        return STATUS_DELINQUENT
    return STATUS_CURRENT
