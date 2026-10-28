"""Status, reconcile, eligibility, concentration for a run."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fence_pipeline.concentration import aggregate_eligible, compute_pool
from fence_pipeline.eligibility import evaluate_eligibility
from fence_pipeline.models import (
    EligibilityResult,
    Installment,
    PoolSnapshot,
    ReceivableCurrent,
    RowException,
    SectorConcentration,
)

RECONCILE_SOURCE = "reconcile"
from fence_pipeline.reconcile import computed_outstanding, outstanding_mismatch
from fence_pipeline.status import STATUS_DEFAULTED, derive_status


def evaluate_run(session: Session, run_id: int) -> dict:
    session.execute(
        delete(RowException).where(
            RowException.run_id == run_id,
            RowException.source == RECONCILE_SOURCE,
        )
    )

    receivables = session.scalars(
        select(ReceivableCurrent).where(ReceivableCurrent.active.is_(True))
    ).all()

    # Sum applied cash on the *current* schedule only (not superseded versions).
    applied_rows = session.execute(
        select(
            Installment.receivable_external_id,
            func.coalesce(func.sum(Installment.amount_applied), 0).label(
                "total_applied"
            ),
        )
        .join(
            ReceivableCurrent,
            (
                ReceivableCurrent.receivable_external_id
                == Installment.receivable_external_id
            )
            & (
                ReceivableCurrent.current_schedule_version
                == Installment.schedule_version
            ),
        )
        .where(ReceivableCurrent.active.is_(True))
        .group_by(Installment.receivable_external_id)
    ).all()
    ## making a dict of "external_id: total_applied"
    applied_by_recv = {
        row.receivable_external_id: Decimal(row.total_applied) for row in applied_rows
    }

    ## obligor_name -> receivable external ids with Defaulted status
    obligor_defaulted: dict[str, set[str]] = defaultdict(set)
    for recv in receivables:
        status = derive_status(
            Decimal(recv.outstanding_balance), int(recv.days_past_due)
        )
        if status == STATUS_DEFAULTED:
            obligor_defaulted[recv.obligor_name].add(recv.receivable_external_id)

    eligibility_rows: list[EligibilityResult] = []
    eligible_inputs: list[tuple[str, Decimal, bool]] = []
    reconcile_exceptions: list[RowException] = []

    for recv in receivables:
        ext_id = recv.receivable_external_id
        export_out = Decimal(recv.outstanding_balance)
        face = Decimal(recv.face_value)
        total_applied = applied_by_recv.get(ext_id, Decimal("0.00"))
        computed = computed_outstanding(face, total_applied)
        mismatch = outstanding_mismatch(export_out, computed)

        ## finding if any other receivables for the same obligor have defaulted
        other_defaulted = any(
            other_id != ext_id
            for other_id in obligor_defaulted.get(recv.obligor_name, set())
        )

        decision = evaluate_eligibility(
            face_value=face,
            obligor_risk_score=int(recv.obligor_risk_score),
            sector=recv.sector,
            currency=recv.currency,
            outstanding_balance=export_out,
            days_past_due=int(recv.days_past_due),
            obligor_has_other_defaulted=other_defaulted,
        )

        if mismatch:
            delta = export_out - computed
            reconcile_exceptions.append(
                RowException(
                    run_id=run_id,
                    source=RECONCILE_SOURCE,
                    row_key=ext_id,
                    reason=(
                        f"outstanding mismatch: export={export_out} "
                        f"computed={computed} delta={delta}; "
                        "Starfleet should correct balance or payment history "
                        "and resend the export"
                    ),
                )
            )

        eligibility_rows.append(
            EligibilityResult(
                run_id=run_id,
                receivable_external_id=ext_id,
                status=decision.status,
                eligible=decision.eligible,
                reasons="; ".join(decision.reasons),
                export_outstanding=export_out,
                computed_outstanding=computed,
                outstanding_mismatch=mismatch,
                sector=recv.sector,
            )
        )
        eligible_inputs.append((recv.sector, export_out, decision.eligible))

    by_sector = aggregate_eligible(eligible_inputs)
    pool = compute_pool(by_sector)

    session.execute(
        delete(EligibilityResult).where(EligibilityResult.run_id == run_id)
    )
    session.add_all(eligibility_rows)
    if reconcile_exceptions:
        session.add_all(reconcile_exceptions)

    session.execute(
        delete(SectorConcentration).where(SectorConcentration.run_id == run_id)
    )
    session.add_all(
        [
            SectorConcentration(
                run_id=run_id,
                sector=s.sector,
                eligible_outstanding=s.eligible_outstanding,
                share=s.share,
                excess=s.excess,
                within_cap=s.within_cap,
            )
            for s in pool.sectors
        ]
    )

    exception_count = session.scalar(
        select(func.count()).select_from(RowException).where(
            RowException.run_id == run_id
        )
    ) or 0

    eligible_count = sum(1 for r in eligibility_rows if r.eligible)
    ineligible_count = len(eligibility_rows) - eligible_count

    session.execute(
        pg_insert(PoolSnapshot)
        .values(
            run_id=run_id,
            total_processed=len(eligibility_rows),
            eligible_count=eligible_count,
            ineligible_count=ineligible_count,
            exception_count=int(exception_count),
            aeb=pool.aeb,
            excess_concentration=pool.excess_concentration,
            borrowing_base=pool.borrowing_base,
            facility_limit=pool.facility_limit,
        )
        .on_conflict_do_update(
            index_elements=["run_id"],
            set_={
                "total_processed": len(eligibility_rows),
                "eligible_count": eligible_count,
                "ineligible_count": ineligible_count,
                "exception_count": int(exception_count),
                "aeb": pool.aeb,
                "excess_concentration": pool.excess_concentration,
                "borrowing_base": pool.borrowing_base,
                "facility_limit": pool.facility_limit,
            },
        )
    )
    session.commit()

    return {
        "total_processed": len(eligibility_rows),
        "eligible_count": eligible_count,
        "ineligible_count": ineligible_count,
        "exception_count": int(exception_count),
        "aeb": str(pool.aeb),
        "excess_concentration": str(pool.excess_concentration),
        "borrowing_base": str(pool.borrowing_base),
    }
