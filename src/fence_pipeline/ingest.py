"""Soft-fail CSV ingest for receivables and collections."""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fence_pipeline.models import PaymentEvent, ReceivableEvent, RowException
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
