"""Orphan payments stay pending until the receivable is active."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from fence_pipeline.db import ensure_schema, sqlalchemy_url, truncate_all
from fence_pipeline.models import Installment, PaymentApplied, RowException
from fence_pipeline.pipeline import run_pipeline

VERIFY_DSN = "postgresql://fence:fence@localhost:5432/fence_verify"

RECEIVABLE_HEADER = (
    "receivable_external_id,merchant_name,obligor_name,sector,purchase_date,"
    "face_value,purchase_price,number_of_installments,currency,obligor_risk_score,"
    "outstanding_balance,days_past_due"
)


def _db_available() -> bool:
    try:
        engine = create_engine(sqlalchemy_url(VERIFY_DSN), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _write_collections(path: Path, rows: list[str]) -> None:
    body = "payment_id,receivable_external_id,payment_date,amount\n"
    if rows:
        body += "\n".join(rows) + "\n"
    path.write_text(body, encoding="utf-8")


@pytest.mark.skipif(not _db_available(), reason="Postgres fence_verify not available")
def test_orphan_held_then_applied_when_receivable_arrives(tmp_path: Path):
    ensure_schema(VERIFY_DSN)
    engine = create_engine(sqlalchemy_url(VERIFY_DSN))
    with Session(engine) as session:
        truncate_all(session)

    empty_recv = tmp_path / "recv_empty.csv"
    later_recv = tmp_path / "recv_later.csv"
    collections = tmp_path / "collections.csv"
    empty_recv.write_text(RECEIVABLE_HEADER + "\n", encoding="utf-8")
    later_recv.write_text(
        RECEIVABLE_HEADER
        + "\n"
        + "R-LATE,Merchant,Obligor,Retail,2024-06-01,1000.00,990.00,1,USD,80,1000.00,0\n",
        encoding="utf-8",
    )
    _write_collections(collections, ["P-ORPH,R-LATE,2024-07-01,100.00"])

    run1 = run_pipeline(empty_recv, collections, tmp_path / "out1", dsn=VERIFY_DSN)
    with Session(engine) as session:
        assert session.get(PaymentApplied, "P-ORPH") is None
        orphans = session.scalars(
            select(RowException).where(
                RowException.run_id == run1,
                RowException.source == "allocate",
            )
        ).all()
        assert len(orphans) == 1
        assert "held" in orphans[0].reason
        assert orphans[0].row_key == "P-ORPH"

    run2 = run_pipeline(later_recv, collections, tmp_path / "out2", dsn=VERIFY_DSN)
    with Session(engine) as session:
        assert session.get(PaymentApplied, "P-ORPH") is not None
        applied = session.scalar(
            select(Installment.amount_applied).where(
                Installment.receivable_external_id == "R-LATE",
                Installment.seq == 1,
            )
        )
        assert Decimal(applied) == Decimal("100.00")
        assert run2 != run1
