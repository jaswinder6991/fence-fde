"""Expected payment schedule generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fence_pipeline.parse import add_months, cents_to_money, money_to_cents


@dataclass(frozen=True)
class InstallmentSpec:
    seq: int
    due_date: date
    amount_due: Decimal


def build_schedule(
    purchase_date: date,
    face_value: Decimal,
    number_of_installments: int,
) -> list[InstallmentSpec]:
    """Equal installments; remainder cents on the last installment.

    First installment is one month after purchase_date.
    """
    if number_of_installments < 1:
        raise ValueError("number_of_installments must be >= 1")

    total_cents = money_to_cents(face_value)
    base = total_cents // number_of_installments
    remainder = total_cents - base * number_of_installments

    specs: list[InstallmentSpec] = []
    for i in range(1, number_of_installments + 1):
        cents = base + (remainder if i == number_of_installments else 0)
        specs.append(
            InstallmentSpec(
                seq=i,
                due_date=add_months(purchase_date, i),
                amount_due=cents_to_money(cents),
            )
        )
    return specs
