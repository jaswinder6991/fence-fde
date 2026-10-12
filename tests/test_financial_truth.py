"""Ground truth from export CSVs, compared to pipeline reports."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fence_pipeline.db import ensure_schema, sqlalchemy_url, truncate_all
from fence_pipeline.pipeline import run_pipeline
from tests.support.compare import compare_to_reports, write_truth_csvs
from tests.support.ground_truth import compute_ground_truth

FIXTURES = Path(__file__).parent / "fixtures"
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


def _write_receivable(path: Path, rows: list[str]) -> None:
    path.write_text(RECEIVABLE_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _write_collections(path: Path, rows: list[str]) -> None:
    body = "payment_id,receivable_external_id,payment_date,amount\n"
    if rows:
        body += "\n".join(rows) + "\n"
    path.write_text(body, encoding="utf-8")


def test_truth_aeb_and_borrowing_base(tmp_path: Path):
    receivables = tmp_path / "receivables.csv"
    collections = tmp_path / "collections.csv"
    _write_receivable(
        receivables,
        [
            "R-RETAIL,Merchant,Obligor A,Retail,2024-01-15,60000.00,59000.00,2,USD,80,60000.00,0",
            "R-LOG,Merchant,Obligor B,Logistics,2024-01-15,40000.00,39000.00,2,USD,80,40000.00,0",
        ],
    )
    _write_collections(collections, [])

    truth = compute_ground_truth(receivables, collections)

    assert truth.pool.aeb == Decimal("100000.00")
    assert truth.pool.excess_concentration == Decimal("20000.00")
    assert truth.pool.borrowing_base == Decimal("68000.00")
    assert truth.pool.facility_limit == Decimal("250000000")
    retail = next(s for s in truth.pool.sectors if s.sector == "retail")
    logistics = next(s for s in truth.pool.sectors if s.sector == "logistics")
    assert retail.eligible_outstanding == Decimal("60000.00")
    assert retail.excess == Decimal("20000.00")
    assert retail.within_cap is False
    assert logistics.eligible_outstanding == Decimal("40000.00")
    assert logistics.excess == Decimal("0.00")
    assert logistics.within_cap is True
    assert truth.pool.eligible_count == 2
    assert truth.payment_ids_seen == 0


def test_duplicate_payment_id_does_not_change_allocation(tmp_path: Path):
    receivables = tmp_path / "receivables.csv"
    once = tmp_path / "once.csv"
    twice = tmp_path / "twice.csv"
    _write_receivable(
        receivables,
        [
            "R1,Merchant,Obligor,Retail,2024-06-01,1000.00,990.00,1,USD,80,1000.00,0",
        ],
    )
    _write_collections(once, ["P1,R1,2024-07-01,100.00"])
    _write_collections(
        twice,
        [
            "P1,R1,2024-07-01,100.00",
            "P1,R1,2024-08-01,500.00",
        ],
    )

    clean = compute_ground_truth(receivables, once)
    duped = compute_ground_truth(receivables, twice)

    assert duped.payment_ids_seen == 2
    assert duped.payment_ids_unique == 1
    assert clean.payment_ids_unique == 1
    assert duped.receivables[0].installments[0].amount_applied == Decimal("100.00")
    assert (
        duped.receivables[0].installments[0].amount_applied
        == clean.receivables[0].installments[0].amount_applied
    )
    assert duped.receivables[0].installments[0].hits == clean.receivables[0].installments[0].hits
    assert duped.receivables[0].installments[0].hits[0].amount == Decimal("100.00")
    assert duped.receivables[0].computed_outstanding == Decimal("900.00")


@pytest.mark.skipif(not _db_available(), reason="Postgres fence_verify not available")
def test_fixture_truth_matches_pipeline_reports(tmp_path: Path):
    ensure_schema(VERIFY_DSN)
    engine = create_engine(sqlalchemy_url(VERIFY_DSN))
    with Session(engine) as session:
        truncate_all(session)

    run_id = run_pipeline(
        FIXTURES / "receivables.csv",
        FIXTURES / "collections.csv",
        tmp_path,
        dsn=VERIFY_DSN,
    )
    truth = compute_ground_truth(
        FIXTURES / "receivables.csv",
        FIXTURES / "collections.csv",
    )
    assert truth.pool.aeb == Decimal("5000.00")
    assert truth.pool.excess_concentration == Decimal("3000.00")
    assert truth.pool.borrowing_base == Decimal("1700.00")
    assert truth.receivables[0].computed_outstanding == Decimal("5000.00")
    assert truth.receivables[0].export_outstanding == Decimal("5000.00")
    assert truth.receivables[0].status == "Current"
    assert truth.receivables[0].eligible is True

    result = compare_to_reports(
        truth,
        tmp_path / f"payment_schedule_report_run_{run_id}.csv",
        tmp_path / f"receivables_position_report_run_{run_id}.csv",
        tmp_path / f"pool_report_run_{run_id}.csv",
    )
    assert result.ok, result.failed

    truth_dir = tmp_path / "truth"
    write_truth_csvs(truth, truth_dir)
    assert (truth_dir / "truth_pool.csv").is_file()
    with (truth_dir / "truth_schedule.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    paid = [r for r in rows if r["payment_id"] == "P-TEST-1"]
    assert len(paid) == 1
    assert Decimal(paid[0]["amount_from_payment"]) == Decimal("2500.00")
