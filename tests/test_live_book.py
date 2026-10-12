"""Live receivables_current: upsert + inactivate, not per-run clones."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from fence_pipeline.db import ensure_schema, sqlalchemy_url, truncate_all
from fence_pipeline.models import EligibilityResult, ReceivableCurrent
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


def _write_receivables(path: Path, rows: list[str]) -> None:
    path.write_text(RECEIVABLE_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _write_collections(path: Path) -> None:
    path.write_text(
        "payment_id,receivable_external_id,payment_date,amount\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(not _db_available(), reason="Postgres fence_verify not available")
def test_second_export_inactivates_missing_ids(tmp_path: Path):
    ensure_schema(VERIFY_DSN)
    engine = create_engine(sqlalchemy_url(VERIFY_DSN))
    with Session(engine) as session:
        truncate_all(session)

    first = tmp_path / "recv1.csv"
    second = tmp_path / "recv2.csv"
    collections = tmp_path / "collections.csv"
    _write_collections(collections)
    _write_receivables(
        first,
        [
            "R-KEEP,Merchant,Obligor A,Retail,2024-01-15,1000.00,990.00,1,USD,80,1000.00,0",
            "R-DROP,Merchant,Obligor B,Retail,2024-01-15,2000.00,1980.00,1,USD,80,2000.00,0",
        ],
    )
    _write_receivables(
        second,
        [
            "R-KEEP,Merchant,Obligor A,Retail,2024-01-15,1000.00,990.00,1,USD,80,500.00,0",
        ],
    )

    run_pipeline(first, collections, tmp_path / "out1", dsn=VERIFY_DSN)
    run_pipeline(second, collections, tmp_path / "out2", dsn=VERIFY_DSN)

    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(ReceivableCurrent))
        assert total == 2
        keep = session.get(ReceivableCurrent, "R-KEEP")
        drop = session.get(ReceivableCurrent, "R-DROP")
        assert keep is not None and keep.active is True
        assert keep.outstanding_balance == Decimal("500.00")
        assert drop is not None and drop.active is False

        active_elig = session.scalars(
            select(EligibilityResult.receivable_external_id).where(
                EligibilityResult.run_id
                == session.scalar(select(func.max(EligibilityResult.run_id)))
            )
        ).all()
        assert set(active_elig) == {"R-KEEP"}
