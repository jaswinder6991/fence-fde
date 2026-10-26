"""Expected payment schedule generation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from fence_pipeline.models import Installment, ReceivableCurrent, RowException
from fence_pipeline.parse import add_months, cents_to_money, money_to_cents

INSTALLMENT_INSERT_BATCH = 1000


@dataclass(frozen=True)
class InstallmentSpec:
    seq: int
    due_date: date
    amount_due: Decimal


def build_missing_schedules(session: Session, run_id: int) -> dict[str, int]:
    """Create installments for active receivables that have none yet.

    Reads only the active live book and its installments (Core columns), not
    historical rows for inactivated receivables.
    """
    created = 0
    drift = 0

    ## getting the active receivables
    receivables = session.execute(
        select(
            ReceivableCurrent.receivable_external_id,
            ReceivableCurrent.purchase_date,
            ReceivableCurrent.face_value,
            ReceivableCurrent.number_of_installments,
        ).where(ReceivableCurrent.active.is_(True))
    ).all()

    ## getting the installments for the active receivables
    inst_rows = session.execute(
        select(
            Installment.receivable_external_id,
            Installment.seq,
            Installment.due_date,
            Installment.amount_due,
        )
        .join(
            ReceivableCurrent,
            ReceivableCurrent.receivable_external_id
            == Installment.receivable_external_id,
        )
        .where(ReceivableCurrent.active.is_(True))
        .order_by(Installment.receivable_external_id, Installment.seq)
    ).all()
    
    ## creating a dictionary to store the installments by receivable external id
    by_recv: dict[str, list] = defaultdict(list)
    for row in inst_rows:
        by_recv[row.receivable_external_id].append(row)

    ## creating a list to store the new installments
    new_rows: list[dict] = []
    for recv in receivables:
        ext_id = recv.receivable_external_id
        ## getting the existing installments for the receivable
        existing = by_recv.get(ext_id, [])
        expected = build_schedule(
            recv.purchase_date,
            Decimal(recv.face_value),
            int(recv.number_of_installments),
        )
        
        if not existing:
            for spec in expected:
                new_rows.append(
                    {
                        "receivable_external_id": ext_id,
                        "seq": spec.seq,
                        "due_date": spec.due_date,
                        "amount_due": spec.amount_due,
                        "amount_applied": Decimal("0.00"),
                    }
                )
            created += 1
            continue

        ## checking if the number of installments is different
        if len(existing) != len(expected):
            drift += 1
            session.add(
                RowException(
                    run_id=run_id,
                    source="schedule",
                    row_key=ext_id,
                    reason=(
                        "unsupported schedule change: installment count differs; "
                        "keeping original schedule — Starfleet should correct "
                        "the export or confirm intentional change"
                    ),
                )
            )
            continue

        ## checking if the due date or amount is different
        mismatched = any(
            row.due_date != spec.due_date
            or Decimal(row.amount_due) != spec.amount_due
            for row, spec in zip(existing, expected, strict=True) ## zip is used to iterate over two lists simultaneously
        )
        if mismatched:
            drift += 1
            session.add(
                RowException(
                    run_id=run_id,
                    source="schedule",
                    row_key=ext_id,
                    reason=(
                        "unsupported schedule change: due date or amount differs; "
                        "keeping original schedule — Starfleet should correct "
                        "the export or confirm intentional change"
                    ),
                )
            )

    for i in range(0, len(new_rows), INSTALLMENT_INSERT_BATCH):
        session.execute(insert(Installment), new_rows[i : i + INSTALLMENT_INSERT_BATCH])
    session.commit()
    return {"created": created, "drift_flagged": drift}


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
        ## adding the remainder to the last installment
        cents = base + (remainder if i == number_of_installments else 0)
        specs.append(
            InstallmentSpec(
                seq=i,
                due_date=add_months(purchase_date, i),
                amount_due=cents_to_money(cents),
            )
        )
    return specs
