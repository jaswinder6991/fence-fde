from datetime import date
from decimal import Decimal

from fence_pipeline.schedule import build_schedule


def test_equal_installments_exact():
    specs = build_schedule(date(2025, 3, 10), Decimal("10000"), 4)
    assert len(specs) == 4
    assert all(s.amount_due == Decimal("2500.00") for s in specs)
    assert specs[0].due_date == date(2025, 4, 10)
    assert specs[3].due_date == date(2025, 7, 10)


def test_remainder_on_last_installment():
    # 100.00 / 3 = 33.33, 33.33, 33.34
    specs = build_schedule(date(2025, 1, 15), Decimal("100.00"), 3)
    assert [s.amount_due for s in specs] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert sum(s.amount_due for s in specs) == Decimal("100.00")


def test_month_end_anchor_stays_on_purchase_day():
    """31 Jan → 28 Feb, then 31 Mar, 30 Apr (not 28 Mar drift)."""
    specs = build_schedule(date(2025, 1, 31), Decimal("1000.00"), 4)
    assert [s.due_date for s in specs] == [
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
        date(2025, 5, 31),
    ]


def test_leap_year_month_end_anchor():
    specs = build_schedule(date(2024, 1, 31), Decimal("100.00"), 2)
    assert [s.due_date for s in specs] == [
        date(2024, 2, 29),
        date(2024, 3, 31),
    ]
