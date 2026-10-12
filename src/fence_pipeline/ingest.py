"""Soft-fail CSV ingest for receivables and collections."""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fence_pipeline.models import (
    PaymentEvent,
    ReceivableCurrent,
    ReceivableEvent,
    RowException,
)
from fence_pipeline.parse import ParseError, parse_payment_row, parse_receivable_row

BATCH_SIZE = 1000


def ingest_receivables(session: Session, run_id: int, path: Path) -> dict[str, int]:
    ok = 0
    failed = 0
    batch: list[ReceivableEvent] = []

    def flush() -> None:
        if not batch:
            return
        session.add_all(batch)
        session.flush()
        batch.clear()

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_number, row in enumerate(reader, start=2):
            try:
                parsed = parse_receivable_row(row)
            except ParseError as exc:
                failed += 1
                key = (
                    (row.get("receivable_external_id") or "").strip()
                    or f"row:{row_number}"
                )
                session.add(
                    RowException(
                        run_id=run_id,
                        source="receivables",
                        row_key=key,
                        reason=str(exc),
                    )
                )
                batch.append(
                    ReceivableEvent(
                        run_id=run_id,
                        row_number=row_number,
                        receivable_external_id=(
                            (row.get("receivable_external_id") or "").strip() or None
                        ),
                        parse_ok=False,
                        parse_error=str(exc),
                    )
                )
                if len(batch) >= BATCH_SIZE:
                    flush()
                continue

            ok += 1
            batch.append(
                ReceivableEvent(
                    run_id=run_id,
                    row_number=row_number,
                    receivable_external_id=parsed.receivable_external_id,
                    merchant_name=parsed.merchant_name,
                    obligor_name=parsed.obligor_name,
                    sector_raw=parsed.sector_raw,
                    sector=parsed.sector,
                    purchase_date=parsed.purchase_date,
                    face_value=parsed.face_value,
                    purchase_price=parsed.purchase_price,
                    number_of_installments=parsed.number_of_installments,
                    currency=parsed.currency,
                    obligor_risk_score=parsed.obligor_risk_score,
                    outstanding_balance=parsed.outstanding_balance,
                    days_past_due=parsed.days_past_due,
                    parse_ok=True,
                    parse_error=None,
                )
            )
            if len(batch) >= BATCH_SIZE:
                flush()

    flush()
    session.commit()
    return {"ok": ok, "failed": failed}


def ingest_collections(session: Session, run_id: int, path: Path) -> dict[str, int]:
    """Insert new payment_ids only; skip duplicates and soft-fail bad rows."""
    inserted = 0
    skipped_existing = 0
    failed = 0
    batch: list[dict] = []
    exceptions: list[RowException] = []

    def flush() -> None:
        nonlocal inserted, skipped_existing
        if exceptions:
            session.add_all(exceptions)
            exceptions.clear()
        if not batch:
            session.flush()
            return
        stmt = (
            pg_insert(PaymentEvent)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["payment_id"])
            .returning(PaymentEvent.payment_id)
        )
        result = session.execute(stmt)
        n = len(result.fetchall())
        inserted += n
        skipped_existing += len(batch) - n
        batch.clear()
        session.flush()

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_number, row in enumerate(reader, start=2):
            try:
                parsed = parse_payment_row(row)
            except ParseError as exc:
                failed += 1
                key = (row.get("payment_id") or "").strip() or f"row:{row_number}"
                exceptions.append(
                    RowException(
                        run_id=run_id,
                        source="collections",
                        row_key=key,
                        reason=str(exc),
                    )
                )
                if len(exceptions) >= BATCH_SIZE:
                    flush()
                continue

            batch.append(
                {
                    "payment_id": parsed.payment_id,
                    "run_id_first_seen": run_id,
                    "receivable_external_id": parsed.receivable_external_id,
                    "payment_date": parsed.payment_date,
                    "amount": parsed.amount,
                }
            )
            if len(batch) >= BATCH_SIZE:
                flush()

    flush()
    session.commit()
    return {
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "failed": failed,
    }


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
