"""Project receivable events into the live book (receivables_current)."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fence_pipeline.models import ReceivableCurrent, ReceivableEvent

BATCH_SIZE = 1000


def project_receivables_current(session: Session, run_id: int) -> dict[str, int]:
    """Upsert tonight's export into the live book; inactivate IDs not in the file.

    Full snapshot semantics: every successfully parsed receivable in this run
    becomes active with refreshed attributes. Rows still in receivables_current
    but absent from tonight's events are set active=False (installments kept).
    """
    events = session.scalars(
        select(ReceivableEvent)
        .where(ReceivableEvent.run_id == run_id, ReceivableEvent.parse_ok.is_(True))
        .order_by(
            ReceivableEvent.receivable_external_id,
            ReceivableEvent.row_number.desc(),
        )
    ).all()

    ## One row per ext_id; events ordered by row_number desc so last CSV row wins
    latest: dict[str, ReceivableEvent] = {}
    for event in events:
        ext_id = event.receivable_external_id
        if ext_id is None or ext_id in latest:
            continue
        latest[ext_id] = event

    rows = [
        {
            "receivable_external_id": ext_id,
            "active": True,
            "last_seen_run_id": run_id,
            "merchant_name": event.merchant_name or "",
            "obligor_name": event.obligor_name or "",
            "sector": event.sector or "",
            "purchase_date": event.purchase_date,
            "face_value": event.face_value,
            "purchase_price": event.purchase_price,
            "number_of_installments": event.number_of_installments or 0,
            "currency": event.currency or "",
            "obligor_risk_score": event.obligor_risk_score or 0,
            "outstanding_balance": event.outstanding_balance,
            "days_past_due": event.days_past_due or 0,
        }
        for ext_id, event in latest.items()
    ]
    upserted = len(rows)
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        insert_stmt = pg_insert(ReceivableCurrent).values(chunk)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[ReceivableCurrent.receivable_external_id],
            set_={
                "active": insert_stmt.excluded.active,
                "last_seen_run_id": insert_stmt.excluded.last_seen_run_id,
                "merchant_name": insert_stmt.excluded.merchant_name,
                "obligor_name": insert_stmt.excluded.obligor_name,
                "sector": insert_stmt.excluded.sector,
                "purchase_date": insert_stmt.excluded.purchase_date,
                "face_value": insert_stmt.excluded.face_value,
                "purchase_price": insert_stmt.excluded.purchase_price,
                "number_of_installments": insert_stmt.excluded.number_of_installments,
                "currency": insert_stmt.excluded.currency,
                "obligor_risk_score": insert_stmt.excluded.obligor_risk_score,
                "outstanding_balance": insert_stmt.excluded.outstanding_balance,
                "days_past_due": insert_stmt.excluded.days_past_due,
            },
        )
        session.execute(upsert_stmt)

    # Tonight's upserts stamp last_seen_run_id. Anything still active with an
    # older stamp was absent from this full snapshot — no client-side ID list.
    result = session.execute(
        update(ReceivableCurrent)
        .where(
            ReceivableCurrent.active.is_(True),
            ReceivableCurrent.last_seen_run_id != run_id,
        )
        .values(active=False)
    )
    inactivated = result.rowcount or 0

    session.commit()
    return {"upserted": upserted, "inactivated": inactivated, "active": upserted}
