"""SQLAlchemy ORM models for the Starfleet facility pipeline."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


Money = Numeric(18, 2)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    receivables_path: Mapped[str] = mapped_column(Text, nullable=False)
    collections_path: Mapped[str] = mapped_column(Text, nullable=False)
    output_dir: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    current_stage: Mapped[str] = mapped_column(Text, nullable=False, default="start")
    error: Mapped[Optional[str]] = mapped_column(Text)
    timings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


class ReceivableEvent(Base):
    __tablename__ = "receivable_events"
    __table_args__ = (
        Index("idx_receivable_events_run", "run_id"),
        Index("idx_receivable_events_ext", "run_id", "receivable_external_id"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    row_number: Mapped[Optional[int]] = mapped_column(Integer)
    receivable_external_id: Mapped[Optional[str]] = mapped_column(Text)
    merchant_name: Mapped[Optional[str]] = mapped_column(Text)
    obligor_name: Mapped[Optional[str]] = mapped_column(Text)
    sector_raw: Mapped[Optional[str]] = mapped_column(Text)
    sector: Mapped[Optional[str]] = mapped_column(Text)
    purchase_date: Mapped[Optional[date]] = mapped_column(Date)
    face_value: Mapped[Optional[Decimal]] = mapped_column(Money)
    purchase_price: Mapped[Optional[Decimal]] = mapped_column(Money)
    number_of_installments: Mapped[Optional[int]] = mapped_column(Integer)
    currency: Mapped[Optional[str]] = mapped_column(Text)
    obligor_risk_score: Mapped[Optional[int]] = mapped_column(Integer)
    outstanding_balance: Mapped[Optional[Decimal]] = mapped_column(Money)
    days_past_due: Mapped[Optional[int]] = mapped_column(Integer)
    parse_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    parse_error: Mapped[Optional[str]] = mapped_column(Text)


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (Index("idx_payment_events_recv", "receivable_external_id"),)

    payment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id_first_seen: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), nullable=False
    )
    receivable_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReceivableCurrent(Base):
    """Live portfolio working set: one row per receivable_external_id.

    Not FOD status Current (vs Delinquent/Defaulted). Upserted each run;
    IDs missing from tonight's full snapshot stay rowed with active=False.
    """

    __tablename__ = "receivables_current"

    receivable_external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_seen_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), nullable=False
    )
    merchant_name: Mapped[str] = mapped_column(Text, nullable=False)
    obligor_name: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str] = mapped_column(Text, nullable=False)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    face_value: Mapped[Decimal] = mapped_column(Money, nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    number_of_installments: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    obligor_risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    outstanding_balance: Mapped[Decimal] = mapped_column(Money, nullable=False)
    days_past_due: Mapped[int] = mapped_column(Integer, nullable=False)


class Installment(Base):
    __tablename__ = "installments"

    receivable_external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Money, nullable=False)
    amount_applied: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0.00"), server_default="0"
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["receivable_external_id", "installment_seq"],
            ["installments.receivable_external_id", "installments.seq"],
        ),
        Index(
            "idx_payment_allocations_recv",
            "receivable_external_id",
            "installment_seq",
        ),
    )

    payment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("payment_events.payment_id"), primary_key=True
    )
    receivable_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    installment_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PaymentApplied(Base):
    __tablename__ = "payments_applied"

    payment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("payment_events.payment_id"), primary_key=True
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), nullable=False
    )


class EligibilityResult(Base):
    __tablename__ = "eligibility_results"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), primary_key=True
    )
    receivable_external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasons: Mapped[str] = mapped_column(Text, nullable=False, default="")
    export_outstanding: Mapped[Decimal] = mapped_column(Money, nullable=False)
    computed_outstanding: Mapped[Decimal] = mapped_column(Money, nullable=False)
    outstanding_mismatch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    sector: Mapped[str] = mapped_column(Text, nullable=False)


class PoolSnapshot(Base):
    __tablename__ = "pool_snapshots"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), primary_key=True
    )
    total_processed: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ineligible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    exception_count: Mapped[int] = mapped_column(Integer, nullable=False)
    aeb: Mapped[Decimal] = mapped_column(Money, nullable=False)
    excess_concentration: Mapped[Decimal] = mapped_column(Money, nullable=False)
    borrowing_base: Mapped[Decimal] = mapped_column(Money, nullable=False)
    facility_limit: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("250000000"), server_default="250000000"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SectorConcentration(Base):
    __tablename__ = "sector_concentration"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), primary_key=True
    )
    sector: Mapped[str] = mapped_column(Text, primary_key=True)
    eligible_outstanding: Mapped[Decimal] = mapped_column(Money, nullable=False)
    share: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    excess: Mapped[Decimal] = mapped_column(Money, nullable=False)
    within_cap: Mapped[bool] = mapped_column(Boolean, nullable=False)


class RowException(Base):
    __tablename__ = "row_exceptions"
    __table_args__ = (Index("idx_row_exceptions_run", "run_id"),)

    exception_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.run_id"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    row_key: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
