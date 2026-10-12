"""Pure-Python ground truth from export CSVs (no DB)."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from fence_pipeline.concentration import aggregate_eligible, compute_pool
from fence_pipeline.eligibility import evaluate_eligibility
from fence_pipeline.parse import (
    ParseError,
    ParsedPayment,
    ParsedReceivable,
    parse_payment_row,
    parse_receivable_row,
)
from fence_pipeline.reconcile import computed_outstanding
from fence_pipeline.schedule import build_schedule
from fence_pipeline.status import STATUS_DEFAULTED, derive_status


@dataclass(frozen=True)
class AllocationHit:
    payment_id: str
    amount: Decimal


@dataclass(frozen=True)
class InstallmentTruth:
    seq: int
    due_date: date
    amount_due: Decimal
    amount_applied: Decimal
    settlement: str
    hits: tuple[AllocationHit, ...]


@dataclass(frozen=True)
class ReceivableTruth:
    receivable_external_id: str
    sector: str
    face_value: Decimal
    export_outstanding: Decimal
    computed_outstanding: Decimal
    days_past_due: int
    status: str
    eligible: bool
    reasons: tuple[str, ...]
    installments: tuple[InstallmentTruth, ...]


@dataclass(frozen=True)
class SectorTruth:
    sector: str
    eligible_outstanding: Decimal
    share: Decimal
    excess: Decimal
    within_cap: bool


@dataclass(frozen=True)
class PoolTruth:
    total_processed: int
    eligible_count: int
    ineligible_count: int
    aeb: Decimal
    excess_concentration: Decimal
    borrowing_base: Decimal
    facility_limit: Decimal
    sectors: tuple[SectorTruth, ...]


@dataclass(frozen=True)
class GroundTruth:
    receivables: tuple[ReceivableTruth, ...]
    pool: PoolTruth
    payment_ids_seen: int
    payment_ids_unique: int
    parse_failures_receivables: int
    parse_failures_collections: int


def compute_ground_truth(
    receivables_path: Path, collections_path: Path
) -> GroundTruth:
    recv_by_id: dict[str, ParsedReceivable] = {}
    recv_failures = 0
    with receivables_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                parsed = parse_receivable_row(row)
            except ParseError:
                recv_failures += 1
                continue
            recv_by_id[parsed.receivable_external_id] = parsed

    payments: list[ParsedPayment] = []
    seen_ids: set[str] = set()
    pay_failures = 0
    pay_seen = 0
    with collections_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                parsed = parse_payment_row(row)
            except ParseError:
                pay_failures += 1
                continue
            pay_seen += 1
            if parsed.payment_id in seen_ids:
                continue
            seen_ids.add(parsed.payment_id)
            payments.append(parsed)

    payments.sort(key=lambda p: (p.payment_date, p.payment_id))

    schedules: dict[str, list[dict]] = {}
    hits: dict[tuple[str, int], list[AllocationHit]] = defaultdict(list)
    for ext_id, recv in recv_by_id.items():
        specs = build_schedule(
            recv.purchase_date, recv.face_value, recv.number_of_installments
        )
        schedules[ext_id] = [
            {
                "seq": s.seq,
                "due_date": s.due_date,
                "amount_due": s.amount_due,
                "amount_applied": Decimal("0.00"),
            }
            for s in specs
        ]

    for payment in payments:
        ext_id = payment.receivable_external_id
        schedule = schedules.get(ext_id)
        if schedule is None:
            continue
        remaining = Decimal(payment.amount)
        for inst in schedule:
            if remaining <= 0:
                break
            open_amt = inst["amount_due"] - inst["amount_applied"]
            if open_amt <= 0:
                continue
            apply_amt = min(open_amt, remaining)
            inst["amount_applied"] += apply_amt
            hits[(ext_id, inst["seq"])].append(
                AllocationHit(payment_id=payment.payment_id, amount=apply_amt)
            )
            remaining -= apply_amt

    obligor_defaulted: dict[str, set[str]] = defaultdict(set)
    status_by_id: dict[str, str] = {}
    for ext_id, recv in recv_by_id.items():
        status = derive_status(recv.outstanding_balance, recv.days_past_due)
        status_by_id[ext_id] = status
        if status == STATUS_DEFAULTED:
            obligor_defaulted[recv.obligor_name].add(ext_id)

    receivable_truths: list[ReceivableTruth] = []
    eligible_inputs: list[tuple[str, Decimal, bool]] = []

    for ext_id in sorted(recv_by_id):
        recv = recv_by_id[ext_id]
        schedule = schedules[ext_id]
        total_applied = sum(
            (inst["amount_applied"] for inst in schedule), Decimal("0.00")
        )
        computed = computed_outstanding(recv.face_value, total_applied)
        other_defaulted = any(
            other != ext_id for other in obligor_defaulted.get(recv.obligor_name, set())
        )
        decision = evaluate_eligibility(
            face_value=recv.face_value,
            obligor_risk_score=recv.obligor_risk_score,
            sector=recv.sector,
            currency=recv.currency,
            outstanding_balance=recv.outstanding_balance,
            days_past_due=recv.days_past_due,
            obligor_has_other_defaulted=other_defaulted,
        )
        inst_truths: list[InstallmentTruth] = []
        for inst in schedule:
            applied = inst["amount_applied"]
            if applied >= inst["amount_due"]:
                settlement = "settled"
            elif applied > 0:
                settlement = "partial"
            else:
                settlement = "unpaid"
            inst_truths.append(
                InstallmentTruth(
                    seq=inst["seq"],
                    due_date=inst["due_date"],
                    amount_due=inst["amount_due"],
                    amount_applied=applied,
                    settlement=settlement,
                    hits=tuple(hits.get((ext_id, inst["seq"]), ())),
                )
            )
        receivable_truths.append(
            ReceivableTruth(
                receivable_external_id=ext_id,
                sector=recv.sector,
                face_value=recv.face_value,
                export_outstanding=recv.outstanding_balance,
                computed_outstanding=computed,
                days_past_due=recv.days_past_due,
                status=decision.status,
                eligible=decision.eligible,
                reasons=tuple(decision.reasons),
                installments=tuple(inst_truths),
            )
        )
        eligible_inputs.append(
            (recv.sector, recv.outstanding_balance, decision.eligible)
        )

    by_sector = aggregate_eligible(eligible_inputs)
    pool = compute_pool(by_sector)
    eligible_count = sum(1 for r in receivable_truths if r.eligible)
    pool_truth = PoolTruth(
        total_processed=len(receivable_truths),
        eligible_count=eligible_count,
        ineligible_count=len(receivable_truths) - eligible_count,
        aeb=pool.aeb,
        excess_concentration=pool.excess_concentration,
        borrowing_base=pool.borrowing_base,
        facility_limit=pool.facility_limit,
        sectors=tuple(
            SectorTruth(
                sector=s.sector,
                eligible_outstanding=s.eligible_outstanding,
                share=s.share,
                excess=s.excess,
                within_cap=s.within_cap,
            )
            for s in pool.sectors
        ),
    )
    return GroundTruth(
        receivables=tuple(receivable_truths),
        pool=pool_truth,
        payment_ids_seen=pay_seen,
        payment_ids_unique=len(seen_ids),
        parse_failures_receivables=recv_failures,
        parse_failures_collections=pay_failures,
    )
