"""Pure allocation waterfall logic check via schedule + manual apply."""

from datetime import date
from decimal import Decimal

from fence_pipeline.schedule import build_schedule


def test_waterfall_settles_earliest_first():
    specs = build_schedule(date(2025, 1, 1), Decimal("100.00"), 4)
    applied = [Decimal("0")] * 4
    payment = Decimal("60.00")
    remaining = payment
    for i, spec in enumerate(specs):
        if remaining <= 0:
            break
        open_amt = spec.amount_due - applied[i]
        take = min(open_amt, remaining)
        applied[i] += take
        remaining -= take
    assert applied[0] == Decimal("25.00")
    assert applied[1] == Decimal("25.00")
    assert applied[2] == Decimal("10.00")
    assert applied[3] == Decimal("0.00")
    assert remaining == 0
