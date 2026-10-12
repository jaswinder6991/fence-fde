"""Integration test against Compose Postgres (skipped if DB unavailable)."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from fence_pipeline.db import DEFAULT_DSN, ensure_schema, sqlalchemy_url, truncate_all
from fence_pipeline.models import Installment, PaymentEvent
from fence_pipeline.pipeline import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"


def _db_available() -> bool:
    try:
        engine = create_engine(sqlalchemy_url(DEFAULT_DSN), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="Postgres not available on localhost:5432"
)


@pytest.fixture()
def clean_db():
    ensure_schema(DEFAULT_DSN)
    engine = create_engine(sqlalchemy_url(DEFAULT_DSN))
    with Session(engine) as session:
        truncate_all(session)
    yield


def test_end_to_end_fixture(clean_db, tmp_path):
    run_id = run_pipeline(
        FIXTURES / "receivables.csv",
        FIXTURES / "collections.csv",
        tmp_path,
    )
    schedule_report = tmp_path / f"payment_schedule_report_run_{run_id}.csv"
    position_report = tmp_path / f"receivables_position_report_run_{run_id}.csv"
    pool_report = tmp_path / f"pool_report_run_{run_id}.csv"
    pool_html = tmp_path / f"pool_report_run_{run_id}.html"
    exceptions_report = tmp_path / f"exceptions_report_run_{run_id}.csv"
    assert schedule_report.is_file()
    assert position_report.is_file()
    assert pool_report.is_file()
    assert pool_html.is_file()
    assert exceptions_report.is_file()
    html_text = pool_html.read_text(encoding="utf-8")
    assert "Borrowing base" in html_text
    assert exceptions_report.name in html_text

    with schedule_report.open() as f:
        schedule_rows = list(csv.DictReader(f))
    # 4 installments; first two each hit by one payment → 4 rows
    assert len(schedule_rows) == 4
    settled = [r for r in schedule_rows if r["installment_settlement"] == "settled"]
    unpaid = [r for r in schedule_rows if r["installment_settlement"] == "unpaid"]
    assert len(settled) == 2
    assert len(unpaid) == 2
    assert settled[0]["payment_id"] == "P-TEST-1"
    assert settled[1]["payment_id"] == "P-TEST-2"
    assert Decimal(settled[0]["amount_from_payment"]) == Decimal("2500.00")
    assert unpaid[0]["payment_id"] == ""

    with position_report.open() as f:
        position_rows = list(csv.DictReader(f))
    assert len(position_rows) == 1
    assert position_rows[0]["status"] == "Current"
    assert position_rows[0]["eligible"] in ("True", "true", True)
    assert Decimal(position_rows[0]["export_outstanding"]) == Decimal("5000.00")
    assert "installment_seq" not in position_rows[0]

    with pool_report.open() as f:
        pool_rows = list(csv.reader(f))
    metrics = {
        (section, key): value
        for section, key, value in pool_rows[1:]
        if section == "current"
    }
    assert metrics[("current", "total_processed")] == "1"
    assert Decimal(metrics[("current", "borrowing_base")]) > 0

    # Second run with same payments must not double-apply
    run_id2 = run_pipeline(
        FIXTURES / "receivables.csv",
        FIXTURES / "collections.csv",
        tmp_path,
    )
    engine = create_engine(sqlalchemy_url(DEFAULT_DSN))
    with Session(engine) as session:
        amount = session.scalar(
            select(Installment.amount_applied).where(
                Installment.receivable_external_id == "SL-TEST-1",
                Installment.seq == 1,
            )
        )
        assert Decimal(amount) == Decimal("2500.00")
        assert len(session.scalars(select(PaymentEvent)).all()) == 2
    assert run_id2 != run_id
