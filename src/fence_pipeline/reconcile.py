"""Outstanding balance reconciliation vs export."""

from __future__ import annotations

from decimal import Decimal


def computed_outstanding(face_value: Decimal, total_applied: Decimal) -> Decimal:
    remaining = face_value - total_applied
    if remaining < 0:
        remaining = Decimal("0.00")
    return remaining.quantize(Decimal("0.01"))


def outstanding_mismatch(
    export_outstanding: Decimal, computed: Decimal
) -> bool:
    return export_outstanding.quantize(Decimal("0.01")) != computed
