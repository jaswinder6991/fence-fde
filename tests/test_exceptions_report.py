"""Exceptions are the Starfleet correction channel."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from fence_pipeline.db import ensure_schema, sqlalchemy_url, truncate_all
from fence_pipeline.models import RowException
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


@pytest.mark.skipif(not _db_available(), reason="Postgres fence_verify not available")
def test_outstanding_mismatch_becomes_reconcile_exception(tmp_path: Path):
    ensure_schema(VERIFY_DSN)
    engine = create_engine(sqlalchemy_url(VERIFY_DSN))
    with Session(engine) as session:
        truncate_all(session)

    receivables = tmp_path / "receivables.csv"
    collections = tmp_path / "collections.csv"
    # Export claims 9000 still outstanding after a 2500 payment on 10000 face.
    receivables.write_text(
        RECEIVABLE_HEADER
        + "\n"
        + "R1,Merchant,Obligor,Retail,2024-06-01,10000.00,9900.00,4,USD,80,9000.00,0\n",
        encoding="utf-8",
    )
    collections.write_text(
        "payment_id,receivable_external_id,payment_date,amount\n"
        "P1,R1,2024-07-01,2500.00\n",
        encoding="utf-8",
    )

    run_id = run_pipeline(receivables, collections, tmp_path / "out", dsn=VERIFY_DSN)

    with Session(engine) as session:
        rows = session.scalars(
            select(RowException).where(
                RowException.run_id == run_id,
                RowException.source == "reconcile",
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].row_key == "R1"
        assert "export=9000.00" in rows[0].reason
        assert "computed=7500.00" in rows[0].reason

    exc_path = tmp_path / "out" / f"exceptions_report_run_{run_id}.csv"
    with exc_path.open(encoding="utf-8") as f:
        sheet = list(csv.DictReader(f))
    assert any(r["source"] == "reconcile" and r["row_key"] == "R1" for r in sheet)
    assert all(r["action"].startswith("Correct") for r in sheet)

    html = (tmp_path / "out" / f"pool_report_run_{run_id}.html").read_text(
        encoding="utf-8"
    )
    assert "reconcile" in html
