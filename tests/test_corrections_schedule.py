"""Correction rebuild: new schedule version, old kept, payments reopened."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from fence_pipeline.db import DEFAULT_DSN, ensure_schema, sqlalchemy_url, truncate_all
from fence_pipeline.models import (
    Installment,
    PaymentAllocation,
    PaymentApplied,
    ReceivableCurrent,
    RowException,
)
from fence_pipeline.pipeline import run_pipeline

CORRECTIONS = Path(__file__).resolve().parents[1] / "data" / "corrections"


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


def test_face_change_rebuilds_schedule_keeps_v1_and_reapplies(clean_db, tmp_path):
    run_pipeline(
        CORRECTIONS / "receivables-1.csv",
        CORRECTIONS / "collections-1.csv",
        tmp_path / "out1",
    )
    run_id2 = run_pipeline(
        CORRECTIONS / "receivables-2.csv",
        CORRECTIONS / "collections-2.csv",
        tmp_path / "out2",
    )

    engine = create_engine(sqlalchemy_url(DEFAULT_DSN))
    with Session(engine) as session:
        # SL-C-1: face 6000→3000, same N=6 → correction
        recv = session.get(ReceivableCurrent, "SL-C-1")
        assert recv is not None
        assert recv.current_schedule_version == 2
        assert Decimal(recv.face_value) == Decimal("3000")

        v1 = session.scalars(
            select(Installment)
            .where(
                Installment.receivable_external_id == "SL-C-1",
                Installment.schedule_version == 1,
            )
            .order_by(Installment.seq)
        ).all()
        v2 = session.scalars(
            select(Installment)
            .where(
                Installment.receivable_external_id == "SL-C-1",
                Installment.schedule_version == 2,
            )
            .order_by(Installment.seq)
        ).all()
        assert len(v1) == 6
        assert all(Decimal(i.amount_due) == Decimal("1000.00") for i in v1)
        assert len(v2) == 6
        assert all(Decimal(i.amount_due) == Decimal("500.00") for i in v2)

        # Old allocations remain on v1 for audit; current cash sits on v2.
        v1_allocs = session.scalars(
            select(PaymentAllocation).where(
                PaymentAllocation.receivable_external_id == "SL-C-1",
                PaymentAllocation.schedule_version == 1,
            )
        ).all()
        v2_allocs = session.scalars(
            select(PaymentAllocation).where(
                PaymentAllocation.receivable_external_id == "SL-C-1",
                PaymentAllocation.schedule_version == 2,
            )
        ).all()
        assert len(v1_allocs) >= 1
        assert len(v2_allocs) >= 1

        applied_v2 = sum((Decimal(i.amount_applied) for i in v2), Decimal("0"))
        assert applied_v2 == Decimal("2000.00")  # two $1000 payments

        corrections = session.scalars(
            select(RowException).where(
                RowException.run_id == run_id2,
                RowException.source == "correction",
                RowException.row_key == "SL-C-1",
            )
        ).all()
        assert len(corrections) == 1
        assert "face 6000.00→3000" in corrections[0].reason.replace("3000.00", "3000")


def test_position_only_does_not_bump_version(clean_db, tmp_path):
    run_pipeline(
        CORRECTIONS / "receivables-1.csv",
        CORRECTIONS / "collections-1.csv",
        tmp_path / "out1",
    )
    run_pipeline(
        CORRECTIONS / "receivables-2.csv",
        CORRECTIONS / "collections-2.csv",
        tmp_path / "out2",
    )

    engine = create_engine(sqlalchemy_url(DEFAULT_DSN))
    with Session(engine) as session:
        # SL-C-2: face/N unchanged → still version 1
        recv = session.get(ReceivableCurrent, "SL-C-2")
        assert recv is not None
        assert recv.current_schedule_version == 1
        versions = session.scalar(
            select(func.count()).select_from(Installment).where(
                Installment.receivable_external_id == "SL-C-2"
            )
        )
        # 3 installments, one version only
        assert versions == 3


def test_overpay_after_correction_fills_schedule_then_reports_excess(clean_db, tmp_path):
    """SL-C-3 / SL-C-6: paid > new face; waterfall fills plan; excess is reported."""
    run_pipeline(
        CORRECTIONS / "receivables-1.csv",
        CORRECTIONS / "collections-1.csv",
        tmp_path / "out1",
    )
    run_id2 = run_pipeline(
        CORRECTIONS / "receivables-2.csv",
        CORRECTIONS / "collections-2.csv",
        tmp_path / "out2",
    )

    engine = create_engine(sqlalchemy_url(DEFAULT_DSN))
    with Session(engine) as session:
        # Both are declared Closed (OB=0) after correction.
        for rid, new_face, expected_applied in (
            ("SL-C-3", Decimal("800"), Decimal("800.00")),
            ("SL-C-6", Decimal("3000"), Decimal("3000.00")),
        ):
            recv = session.get(ReceivableCurrent, rid)
            assert recv is not None
            assert recv.current_schedule_version == 2
            assert Decimal(recv.outstanding_balance) == Decimal("0")
            assert Decimal(recv.face_value) == new_face

            applied = session.scalar(
                select(func.coalesce(func.sum(Installment.amount_applied), 0)).where(
                    Installment.receivable_external_id == rid,
                    Installment.schedule_version == 2,
                )
            )
            assert Decimal(applied) == expected_applied

        overpays = session.scalars(
            select(RowException).where(
                RowException.run_id == run_id2,
                RowException.source == "allocate-excess",
            )
        ).all()
        reasons = " | ".join(e.reason for e in overpays)
        assert "SL-C-3" in reasons
        assert "SL-C-6" in reasons
        assert "status Closed" in reasons
        assert "excess_amount=200.00" in reasons or "excess_amount=200" in reasons
        assert "excess_amount=1000.00" in reasons or "excess_amount=1000" in reasons

        exc_path = tmp_path / "out2" / f"exceptions_report_run_{run_id2}.csv"
        with exc_path.open(encoding="utf-8") as f:
            sheet = list(csv.DictReader(f))
        excess_rows = [r for r in sheet if r["source"] == "allocate-excess"]
        assert excess_rows
        assert all(r["amount"] for r in excess_rows)
        assert {r["amount"] for r in excess_rows} >= {"200.00", "1000.00"}
        assert all(
            "orphan" not in r["action"].lower() for r in excess_rows
        )


def test_correction_run_is_idempotent(clean_db, tmp_path):
    run_pipeline(
        CORRECTIONS / "receivables-1.csv",
        CORRECTIONS / "collections-1.csv",
        tmp_path / "out1",
    )
    run_pipeline(
        CORRECTIONS / "receivables-2.csv",
        CORRECTIONS / "collections-2.csv",
        tmp_path / "out2",
    )
    run_pipeline(
        CORRECTIONS / "receivables-2.csv",
        CORRECTIONS / "collections-2.csv",
        tmp_path / "out3",
    )

    engine = create_engine(sqlalchemy_url(DEFAULT_DSN))
    with Session(engine) as session:
        recv = session.get(ReceivableCurrent, "SL-C-1")
        assert recv is not None
        assert recv.current_schedule_version == 2
        # Still exactly two schedule versions (no v3)
        n_versions = session.scalar(
            select(func.count(func.distinct(Installment.schedule_version))).where(
                Installment.receivable_external_id == "SL-C-1"
            )
        )
        assert n_versions == 2
        # Payments still applied once on current version
        applied_markers = session.scalar(select(func.count()).select_from(PaymentApplied))
        assert applied_markers is not None
        assert applied_markers > 0
