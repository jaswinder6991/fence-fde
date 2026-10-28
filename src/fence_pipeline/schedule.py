"""Expected payment schedule generation (and correction rebuilds)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from fence_pipeline.models import (
    Installment,
    PaymentApplied,
    PaymentEvent,
    ReceivableCurrent,
    RowException,
)
from fence_pipeline.parse import add_months, cents_to_money, money_to_cents

INSTALLMENT_INSERT_BATCH = 1000
CORRECTION_SOURCE = "correction"


@dataclass(frozen=True)
class InstallmentSpec:
    seq: int
    due_date: date
    amount_due: Decimal


def build_missing_schedules(session: Session, run_id: int) -> dict[str, int]:
    """Create current-version schedules; rebuild a new version on term drift.

    Reads only the active live book and its *current* schedule version.
    When declared terms no longer match that plan (face / installment count /
    due dates), keeps the old version for audit, bumps
    ``current_schedule_version``, inserts a new plan, and reopens that
    receivable's payments so allocate can apply them onto the new version.
    """
    created = 0
    corrected = 0

    receivables = session.execute(
        select(
            ReceivableCurrent.receivable_external_id,
            ReceivableCurrent.purchase_date,
            ReceivableCurrent.face_value,
            ReceivableCurrent.number_of_installments,
            ReceivableCurrent.current_schedule_version,
        ).where(ReceivableCurrent.active.is_(True))
    ).all()

    inst_rows = session.execute(
        select(
            Installment.receivable_external_id,
            Installment.seq,
            Installment.due_date,
            Installment.amount_due,
        )
        .join(
            ReceivableCurrent,
            (
                ReceivableCurrent.receivable_external_id
                == Installment.receivable_external_id
            )
            & (
                ReceivableCurrent.current_schedule_version
                == Installment.schedule_version
            ),
        )
        .where(ReceivableCurrent.active.is_(True))
        .order_by(Installment.receivable_external_id, Installment.seq)
    ).all()

    by_recv: dict[str, list] = defaultdict(list)
    for row in inst_rows:
        by_recv[row.receivable_external_id].append(row)

    new_rows: list[dict] = []
    pointer_updates: list[tuple[str, int]] = []
    reopen_receivables: list[str] = []

    for recv in receivables:
        ext_id = recv.receivable_external_id
        version = int(recv.current_schedule_version)
        existing = by_recv.get(ext_id, [])
        expected = build_schedule(
            recv.purchase_date,
            Decimal(recv.face_value),
            int(recv.number_of_installments),
        )

        if not existing:
            for spec in expected:
                new_rows.append(
                    _installment_row(ext_id, version, spec)
                )
            created += 1
            continue

        if not _schedule_differs(existing, expected):
            continue

        # Correction: freeze current version, open a new one from latest terms.
        old_face = sum((Decimal(r.amount_due) for r in existing), Decimal("0.00")) # sum(iterable, start=0)
        new_face = Decimal(recv.face_value)
        old_n = len(existing)
        new_n = len(expected)
        new_version = version + 1

        for spec in expected:
            new_rows.append(_installment_row(ext_id, new_version, spec))
        pointer_updates.append((ext_id, new_version))
        reopen_receivables.append(ext_id)
        corrected += 1
        session.add(
            RowException(
                run_id=run_id,
                source=CORRECTION_SOURCE,
                row_key=ext_id,
                reason=(
                    f"schedule corrected: face {old_face}→{new_face}, "
                    f"installments {old_n}→{new_n}; "
                    f"kept schedule_version {version} for audit; "
                    f"rebuilt as version {new_version}"
                ),
            )
        )

    for i in range(0, len(new_rows), INSTALLMENT_INSERT_BATCH):
        session.execute(insert(Installment), new_rows[i : i + INSTALLMENT_INSERT_BATCH])

    for ext_id, new_version in pointer_updates:
        session.execute(
            update(ReceivableCurrent)
            .where(ReceivableCurrent.receivable_external_id == ext_id)
            .values(current_schedule_version=new_version)
        )

    if reopen_receivables:
        # Drop applied markers only; vN allocations stay for audit.
        # Allocate will re-apply these payment_ids onto the new current version.
        session.execute(
            delete(PaymentApplied).where(
                PaymentApplied.payment_id.in_(
                    select(PaymentEvent.payment_id).where(
                        PaymentEvent.receivable_external_id.in_(reopen_receivables)
                    )
                )
            )
        )

    session.commit()
    return {
        "created": created,
        "corrected": corrected,
        "drift_flagged": 0,
    }


def _installment_row(
    ext_id: str, schedule_version: int, spec: InstallmentSpec
) -> dict:
    return {
        "receivable_external_id": ext_id,
        "schedule_version": schedule_version,
        "seq": spec.seq,
        "due_date": spec.due_date,
        "amount_due": spec.amount_due,
        "amount_applied": Decimal("0.00"),
    }


def _schedule_differs(existing: list, expected: list[InstallmentSpec]) -> bool:
    if len(existing) != len(expected):
        return True
    return any(
        row.due_date != spec.due_date
        or Decimal(row.amount_due) != spec.amount_due
        for row, spec in zip(existing, expected, strict=True)
    )


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
