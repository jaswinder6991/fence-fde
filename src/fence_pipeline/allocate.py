"""Idempotent payment allocation onto installment schedules."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fence_pipeline.models import (
    Installment,
    PaymentAllocation,
    PaymentApplied,
    PaymentEvent,
    ReceivableCurrent,
    RowException,
)
def _cleanup_partial_allocations(session: Session) -> int:
    """Remove allocations for payments that never got an applied marker.

    Uses NOT EXISTS (hash anti-join), not a client-side NOT IN list.
    """
    orphan_alloc = ~select(PaymentApplied.payment_id).where(
        PaymentApplied.payment_id == PaymentAllocation.payment_id
    ).exists()


    touched = session.execute(
        select(
            PaymentAllocation.receivable_external_id,
            PaymentAllocation.installment_seq,
        )
        .where(orphan_alloc)
        .distinct()
    ).all()

    
    result = session.execute(delete(PaymentAllocation).where(orphan_alloc))
    deleted = result.rowcount or 0

    
    for ext_id, seq in touched:
        total = session.scalar(
            select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(
                PaymentAllocation.receivable_external_id == ext_id,
                PaymentAllocation.installment_seq == seq,
            )
        )
        session.execute(
            update(Installment)
            .where(
                Installment.receivable_external_id == ext_id,
                Installment.seq == seq,
            )
            .values(amount_applied=total)
        )
    session.commit()
    return deleted


def allocate_unapplied_payments(
    session: Session, run_id: int, commit_every: int = 1000
) -> dict[str, int]:
    """Allocate payments not yet in payments_applied.

    Schedule state is loaded only for the active live book (Core columns).
    Historical installments on inactivated receivables stay in the DB for audit
    but are not pulled into memory here.
    """
    cleaned = _cleanup_partial_allocations(session)

    applied = 0
    orphans = 0
    no_schedule = 0
    overpay = 0

    ## hashset for active ids for O(1) lookups
    active_ids = set(
        session.scalars(
            select(ReceivableCurrent.receivable_external_id).where(
                ReceivableCurrent.active.is_(True)
            )
        ).all()
    )

    schedules: dict[str, list[dict]] = defaultdict(list)
    ## installments of active receivables
    active_installments = session.execute(
        select(
            Installment.receivable_external_id,
            Installment.seq,
            Installment.amount_due,
            Installment.amount_applied,
        )
        .join(
            ReceivableCurrent,
            ReceivableCurrent.receivable_external_id
            == Installment.receivable_external_id,
        )
        .where(ReceivableCurrent.active.is_(True))
        .order_by(Installment.receivable_external_id, Installment.seq)
    ).all()

    ## example schedules:
    ## {
    ##     "1234567890": [
    ##         {"seq": 1, "amount_due": 100, "amount_applied": 0},
    ##         {"seq": 2, "amount_due": 100, "amount_applied": 0},
    ##     ],
    ## }
    for row in active_installments:
        schedules[row.receivable_external_id].append(
            {
                "seq": row.seq,
                "amount_due": Decimal(row.amount_due),
                "amount_applied": Decimal(row.amount_applied),
            }
        )

    ## payments not yet in payments_applied
    pending = session.execute(
        select(
            PaymentEvent.payment_id,
            PaymentEvent.receivable_external_id,
            PaymentEvent.amount,
        )
        .outerjoin(
            PaymentApplied, PaymentApplied.payment_id == PaymentEvent.payment_id
        )
        .where(PaymentApplied.payment_id.is_(None))
        .order_by(PaymentEvent.payment_date, PaymentEvent.payment_id)
    ).all()


    alloc_buffer: list[dict] = []
    applied_buffer: list[dict] = []
    exception_buffer: list[RowException] = []
    ## example applied_updates:
    ## {
    ##     ("1234567890", 1): 100,
    ##     ("1234567890", 2): 100,
    ## }
    applied_updates: dict[tuple[str, int], Decimal] = {}

    def flush() -> None:
        nonlocal alloc_buffer, applied_buffer, exception_buffer
        if not applied_buffer and not exception_buffer:
            return
        if alloc_buffer:
            session.execute(insert(PaymentAllocation), alloc_buffer)
        for (ext_id, seq), amt in applied_updates.items():
            session.execute(
                update(Installment)
                .where(
                    Installment.receivable_external_id == ext_id,
                    Installment.seq == seq,
                )
                .values(amount_applied=amt)
            )
        if exception_buffer:
            session.add_all(exception_buffer)
        if applied_buffer:
            session.execute(
                pg_insert(PaymentApplied).on_conflict_do_nothing(
                    index_elements=["payment_id"]
                ),
                applied_buffer,
            )
        session.commit()
        alloc_buffer = []
        applied_buffer = []
        exception_buffer = []
        applied_updates.clear()

    for payment in pending:
        payment_id = payment.payment_id
        ext_id = payment.receivable_external_id
        remaining = Decimal(payment.amount)
        # example schedule
        #[
        #    {"seq": 1, "amount_due": 100, "amount_applied": 0},
        #    {"seq": 2, "amount_due": 100, "amount_applied": 0},
        #]
        schedule = schedules.get(ext_id)

        # If the schedule is not found, it means the receivable is not in the active book
        # so we add an exception to the exception buffer and continue
        if schedule is None:
            if ext_id not in active_ids:
                # Hold pending: exception only, no payments_applied. Retries when
                # the receivable enters the active book on a later export.
                orphans += 1
                exception_buffer.append(
                    RowException(
                        run_id=run_id,
                        source="allocate",
                        row_key=payment_id,
                        reason=(
                            f"orphan payment: receivable {ext_id} not in active book; "
                            "payment held (not applied) and will retry on later runs; "
                            "Starfleet should include this receivable on the next "
                            "export or correct/remove the payment_id"
                        ),
                    )
                )
                if len(exception_buffer) >= commit_every:
                    flush()
                continue

            no_schedule += 1
            exception_buffer.append(
                RowException(
                    run_id=run_id,
                    source="allocate",
                    row_key=payment_id,
                    reason=f"no schedule for receivable {ext_id}",
                )
            )
            applied_buffer.append({"payment_id": payment_id, "run_id": run_id})
            applied += 1
            if len(applied_buffer) >= commit_every:
                flush()
            continue

        ## if the schedule is found, we iterate over the installments 
        ## and apply the payment to the installments
        for inst in schedule:
            if remaining <= 0:
                break
            open_amt = inst["amount_due"] - inst["amount_applied"]
            if open_amt <= 0:
                continue
            apply_amt = min(open_amt, remaining)
            inst["amount_applied"] += apply_amt
            applied_updates[(ext_id, inst["seq"])] = inst["amount_applied"]
            alloc_buffer.append(
                {
                    "payment_id": payment_id,
                    "receivable_external_id": ext_id,
                    "installment_seq": inst["seq"],
                    "amount": apply_amt,
                }
            )
            remaining -= apply_amt

        if remaining > 0:
            overpay += 1
            exception_buffer.append(
                RowException(
                    run_id=run_id,
                    source="allocate",
                    row_key=payment_id,
                    reason=(
                        f"payment exceeds remaining schedule by {remaining}; "
                        "Starfleet should verify amount or schedule inputs"
                    ),
                )
            )

        applied_buffer.append({"payment_id": payment_id, "run_id": run_id})
        applied += 1
        if len(applied_buffer) >= commit_every:
            flush()

    flush()
    return {
        "applied": applied,
        "orphans": orphans,
        "no_schedule": no_schedule,
        "overpay": overpay,
        "pending_seen": len(pending),
        "partials_cleaned": cleaned,
    }
