"""Report writers: CSV sheets plus a short HTML pool summary for Starfleet."""

from __future__ import annotations

import csv
import html
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fence_pipeline.models import (
    EligibilityResult,
    Installment,
    PaymentAllocation,
    PipelineRun,
    PoolSnapshot,
    ReceivableCurrent,
    RowException,
    SectorConcentration,
)

# Where Ops/Starfleet should look for full row detail for each exception source.
DEFAULT_DETAIL_SHEETS = {
    "receivables": "receivables_position_report (parse failures may be absent)",
    "collections": "payment_schedule_report / collections export",
    "schedule": "receivables_position_report",
    "correction": "receivables_position_report / payment_schedule_report",
    "allocate": "payment_schedule_report",
    "reconcile": "receivables_position_report (outstanding_mismatch)",
}

DEFAULT_EXCEPTION_ACTIONS = {
    "correction": (
        "Review corrected schedule (prior version kept for audit); "
        "confirm face_value / number_of_installments with Starfleet if unexpected"
    ),
    "allocate": (
        "Review excess or orphan payment; confirm collections / schedule with Starfleet"
    ),
}

# Active receivables per keyset page when writing the schedule CSV.
SCHEDULE_REPORT_CHUNK = 500


def write_payment_schedule_report(session: Session, run_id: int, path: Path) -> int:
    """Schedule + settlement: which payment_id(s) hit each installment.

    One row per payment allocation onto an installment. Unpaid installments
    appear as a single row with empty payment_id.

    Writes in keyset chunks over the active book so installments and allocations
    are never all held in memory at once. Historical rows for inactive
    receivables stay in the DB and are not read here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "receivable_external_id",
        "installment_seq",
        "due_date",
        "amount_due",
        "amount_applied",
        "installment_settlement",
        "payment_id",
        "amount_from_payment",
    ]

    out_rows = 0
    last_id: str | None = None
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        while True:
            id_q = (
                select(ReceivableCurrent.receivable_external_id)
                .where(ReceivableCurrent.active.is_(True))
                .order_by(ReceivableCurrent.receivable_external_id)
                .limit(SCHEDULE_REPORT_CHUNK)
            )
            if last_id is not None:
                id_q = id_q.where(
                    ReceivableCurrent.receivable_external_id > last_id
                )
            chunk_ids = list(session.scalars(id_q).all())
            if not chunk_ids:
                break

            first_id = chunk_ids[0]
            last_id = chunk_ids[-1]

            installments = session.execute(
                select(
                    Installment.receivable_external_id,
                    Installment.seq,
                    Installment.due_date,
                    Installment.amount_due,
                    Installment.amount_applied,
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
                .where(
                    ReceivableCurrent.active.is_(True),
                    Installment.receivable_external_id >= first_id,
                    Installment.receivable_external_id <= last_id,
                )
                .order_by(Installment.receivable_external_id, Installment.seq)
            ).all()

            allocations = session.execute(
                select(
                    PaymentAllocation.receivable_external_id,
                    PaymentAllocation.installment_seq,
                    PaymentAllocation.payment_id,
                    PaymentAllocation.amount,
                )
                .join(
                    ReceivableCurrent,
                    (
                        ReceivableCurrent.receivable_external_id
                        == PaymentAllocation.receivable_external_id
                    )
                    & (
                        ReceivableCurrent.current_schedule_version
                        == PaymentAllocation.schedule_version
                    ),
                )
                .where(
                    ReceivableCurrent.active.is_(True),
                    PaymentAllocation.receivable_external_id >= first_id,
                    PaymentAllocation.receivable_external_id <= last_id,
                )
                .order_by(
                    PaymentAllocation.receivable_external_id,
                    PaymentAllocation.installment_seq,
                    PaymentAllocation.payment_id,
                )
            ).all()

            allocs_by_inst: dict[tuple[str, int], list] = defaultdict(list)
            for alloc in allocations:
                allocs_by_inst[
                    (alloc.receivable_external_id, alloc.installment_seq)
                ].append(alloc)

            for inst in installments:
                if inst.amount_applied >= inst.amount_due:
                    settlement = "settled"
                elif inst.amount_applied > 0:
                    settlement = "partial"
                else:
                    settlement = "unpaid"

                hits = allocs_by_inst.get(
                    (inst.receivable_external_id, inst.seq), []
                )
                if not hits:
                    writer.writerow(
                        {
                            "receivable_external_id": inst.receivable_external_id,
                            "installment_seq": inst.seq,
                            "due_date": inst.due_date,
                            "amount_due": inst.amount_due,
                            "amount_applied": inst.amount_applied,
                            "installment_settlement": settlement,
                            "payment_id": "",
                            "amount_from_payment": "",
                        }
                    )
                    out_rows += 1
                    continue

                for alloc in hits:
                    writer.writerow(
                        {
                            "receivable_external_id": inst.receivable_external_id,
                            "installment_seq": inst.seq,
                            "due_date": inst.due_date,
                            "amount_due": inst.amount_due,
                            "amount_applied": inst.amount_applied,
                            "installment_settlement": settlement,
                            "payment_id": alloc.payment_id,
                            "amount_from_payment": alloc.amount,
                        }
                    )
                    out_rows += 1

    return out_rows


def write_receivables_position_report(
    session: Session, run_id: int, path: Path
) -> int:
    """One row per receivable: position as of this export / Calculation Date."""
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = session.execute(
        select(ReceivableCurrent, EligibilityResult)
        .join(
            EligibilityResult,
            (
                EligibilityResult.receivable_external_id
                == ReceivableCurrent.receivable_external_id
            )
            & (EligibilityResult.run_id == run_id),
        )
        .where(ReceivableCurrent.active.is_(True))
        .order_by(ReceivableCurrent.receivable_external_id)
    ).all()

    fieldnames = [
        "receivable_external_id",
        "merchant_name",
        "obligor_name",
        "sector",
        "purchase_date",
        "face_value",
        "purchase_price",
        "number_of_installments",
        "currency",
        "obligor_risk_score",
        "export_outstanding",
        "computed_outstanding",
        "outstanding_mismatch",
        "days_past_due",
        "status",
        "eligible",
        "ineligibility_reasons",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rc, er in rows:
            writer.writerow(
                {
                    "receivable_external_id": rc.receivable_external_id,
                    "merchant_name": rc.merchant_name,
                    "obligor_name": rc.obligor_name,
                    "sector": rc.sector,
                    "purchase_date": rc.purchase_date,
                    "face_value": rc.face_value,
                    "purchase_price": rc.purchase_price,
                    "number_of_installments": rc.number_of_installments,
                    "currency": rc.currency,
                    "obligor_risk_score": rc.obligor_risk_score,
                    "export_outstanding": rc.outstanding_balance,
                    "computed_outstanding": er.computed_outstanding,
                    "outstanding_mismatch": er.outstanding_mismatch,
                    "days_past_due": rc.days_past_due,
                    "status": er.status,
                    "eligible": er.eligible,
                    "ineligibility_reasons": er.reasons,
                }
            )
    return len(rows)


def write_pool_report(session: Session, run_id: int, path: Path) -> None:
    """Single pool report: totals, concentration, exceptions, vs previous."""
    path.parent.mkdir(parents=True, exist_ok=True)

    current = session.get(PoolSnapshot, run_id)
    if current is None:
        raise RuntimeError(f"no pool_snapshots for run_id={run_id}")

    previous = session.scalars(
        select(PoolSnapshot)
        .where(PoolSnapshot.run_id < run_id)
        .order_by(PoolSnapshot.run_id.desc())
        .limit(1)
    ).first()

    sectors = session.scalars(
        select(SectorConcentration)
        .where(SectorConcentration.run_id == run_id)
        .order_by(SectorConcentration.sector)
    ).all()

    exceptions = session.scalars(
        select(RowException)
        .where(RowException.run_id == run_id)
        .order_by(RowException.exception_id)
    ).all()

    run = session.get(PipelineRun, run_id)
    assert run is not None

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "key", "value"])
        writer.writerow(["run", "run_id", run_id])
        writer.writerow(["run", "started_at", run.started_at.isoformat()])

        for metric in (
            "total_processed",
            "eligible_count",
            "ineligible_count",
            "exception_count",
            "aeb",
            "excess_concentration",
            "borrowing_base",
            "facility_limit",
        ):
            writer.writerow(["current", metric, getattr(current, metric)])
        writer.writerow(
            [
                "current",
                "facility_headroom",
                current.facility_limit - current.borrowing_base,
            ]
        )

        if previous:
            writer.writerow(["previous", "run_id", previous.run_id])
            for metric in (
                "total_processed",
                "eligible_count",
                "ineligible_count",
                "aeb",
                "excess_concentration",
                "borrowing_base",
            ):
                writer.writerow(["previous", metric, getattr(previous, metric)])
                writer.writerow(
                    [
                        "delta",
                        metric,
                        getattr(current, metric) - getattr(previous, metric),
                    ]
                )
        else:
            writer.writerow(
                ["previous", "note", "no prior successful pool snapshot"]
            )

        for s in sectors:
            writer.writerow(
                [
                    "concentration",
                    s.sector,
                    (
                        f"eligible_outstanding={s.eligible_outstanding};"
                        f"share={s.share};"
                        f"excess={s.excess};"
                        f"within_cap={s.within_cap}"
                    ),
                ]
            )

        writer.writerow(["exceptions", "count", len(exceptions)])
        for exc in exceptions:
            writer.writerow(
                [
                    "exception",
                    f"{exc.source}:{exc.row_key or ''}",
                    exc.reason,
                ]
            )


def write_exceptions_report(
    session: Session,
    run_id: int,
    path: Path,
    *,
    detail_sheets: dict[str, str] | None = None,
) -> int:
    """Full exception list for Starfleet correction / resend."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sheets = {**DEFAULT_DETAIL_SHEETS, **(detail_sheets or {})}

    exceptions = session.scalars(
        select(RowException)
        .where(RowException.run_id == run_id)
        .order_by(RowException.exception_id)
    ).all()

    fieldnames = [
        "exception_id",
        "source",
        "row_key",
        "reason",
        "detail_sheet",
        "action",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for exc in exceptions:
            writer.writerow(
                {
                    "exception_id": exc.exception_id,
                    "source": exc.source,
                    "row_key": exc.row_key or "",
                    "reason": exc.reason,
                    "detail_sheet": sheets.get(exc.source, ""),
                    "action": DEFAULT_EXCEPTION_ACTIONS.get(
                        exc.source,
                        "Correct source data and send an updated export",
                    ),
                }
            )
    return len(exceptions)


def write_pool_html_report(
    session: Session,
    run_id: int,
    path: Path,
    *,
    sheet_names: dict[str, str],
) -> None:
    """Human-readable pool summary + exceptions pointing at CSV sheets.

    Payment schedule and full receivables position stay CSV-only (too long for HTML).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    current = session.get(PoolSnapshot, run_id)
    if current is None:
        raise RuntimeError(f"no pool_snapshots for run_id={run_id}")

    previous = session.scalars(
        select(PoolSnapshot)
        .where(PoolSnapshot.run_id < run_id)
        .order_by(PoolSnapshot.run_id.desc())
        .limit(1)
    ).first()

    sectors = session.scalars(
        select(SectorConcentration)
        .where(SectorConcentration.run_id == run_id)
        .order_by(SectorConcentration.sector)
    ).all()

    exceptions = session.scalars(
        select(RowException)
        .where(RowException.run_id == run_id)
        .order_by(RowException.exception_id)
    ).all()

    run = session.get(PipelineRun, run_id)
    assert run is not None

    by_source = Counter(exc.source for exc in exceptions)
    sheets = {**DEFAULT_DETAIL_SHEETS}
    if "position_csv" in sheet_names:
        sheets["receivables"] = sheet_names["position_csv"]
        sheets["schedule"] = sheet_names["position_csv"]
        sheets["correction"] = sheet_names["position_csv"]
        sheets["reconcile"] = sheet_names["position_csv"]
    if "schedule_csv" in sheet_names:
        sheets["collections"] = sheet_names["schedule_csv"]
        sheets["allocate"] = sheet_names["schedule_csv"]
        sheets["correction"] = (
            f"{sheet_names['position_csv']} / {sheet_names['schedule_csv']}"
            if "position_csv" in sheet_names
            else sheet_names["schedule_csv"]
        )

    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    def money(value: object) -> str:
        return f"{value:,}" if value is not None else ""

    metric_rows = [
        ("Total processed", current.total_processed),
        ("Eligible", current.eligible_count),
        ("Ineligible", current.ineligible_count),
        ("Exceptions", current.exception_count),
        ("AEB", money(current.aeb)),
        ("Excess concentration", money(current.excess_concentration)),
        ("Borrowing base", money(current.borrowing_base)),
        ("Facility limit", money(current.facility_limit)),
        (
            "Facility headroom",
            money(current.facility_limit - current.borrowing_base),
        ),
    ]

    delta_html = ""
    if previous:
        delta_bits = []
        for label, attr in (
            ("AEB", "aeb"),
            ("Excess concentration", "excess_concentration"),
            ("Borrowing base", "borrowing_base"),
            ("Eligible", "eligible_count"),
        ):
            cur = getattr(current, attr)
            prev = getattr(previous, attr)
            delta_bits.append(
                f"<tr><th>{esc(label)}</th>"
                f"<td>{esc(money(prev) if not isinstance(prev, int) else prev)}</td>"
                f"<td>{esc(money(cur) if not isinstance(cur, int) else cur)}</td>"
                f"<td>{esc(money(cur - prev) if not isinstance(cur, int) else cur - prev)}</td></tr>"
            )
        delta_html = f"""
<section>
  <h2>Versus previous run ({esc(previous.run_id)})</h2>
  <table>
    <thead><tr><th>Metric</th><th>Previous</th><th>Current</th><th>Delta</th></tr></thead>
    <tbody>
      {"".join(delta_bits)}
    </tbody>
  </table>
</section>
"""
    else:
        delta_html = "<section><h2>Versus previous run</h2><p>No prior pool snapshot.</p></section>"

    sector_rows = "".join(
        f"<tr><td>{esc(s.sector)}</td>"
        f"<td>{esc(money(s.eligible_outstanding))}</td>"
        f"<td>{esc(s.share)}</td>"
        f"<td>{esc(money(s.excess))}</td>"
        f"<td>{'yes' if s.within_cap else 'no'}</td></tr>"
        for s in sectors
    )

    source_rows = "".join(
        f"<tr><td>{esc(source)}</td><td>{count}</td>"
        f"<td><a href=\"{esc(sheets.get(source, ''))}\">{esc(sheets.get(source, ''))}</a></td></tr>"
        for source, count in sorted(by_source.items())
    ) or "<tr><td colspan='3'>No exceptions this run.</td></tr>"

    # Cap inline exception rows so a bad export stays readable; full list is CSV.
    preview_limit = 200
    preview = exceptions[:preview_limit]
    exc_rows = "".join(
        f"<tr><td>{esc(exc.exception_id)}</td>"
        f"<td>{esc(exc.source)}</td>"
        f"<td>{esc(exc.row_key or '')}</td>"
        f"<td>{esc(exc.reason)}</td>"
        f"<td><a href=\"{esc(sheets.get(exc.source, ''))}\">{esc(sheets.get(exc.source, ''))}</a></td></tr>"
        for exc in preview
    ) or "<tr><td colspan='5'>None</td></tr>"
    more_note = ""
    if len(exceptions) > preview_limit:
        more_note = (
            f"<p>Showing {preview_limit} of {len(exceptions)}. "
            f"Full list: <a href=\"{esc(sheet_names.get('exceptions_csv', ''))}\">"
            f"{esc(sheet_names.get('exceptions_csv', 'exceptions CSV'))}</a>.</p>"
        )

    metric_html = "".join(
        f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>"
        for label, value in metric_rows
    )

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Pool report run {esc(run_id)}</title>
  <style>
    :root {{
      --ink: #1a1a1a;
      --muted: #5c5c5c;
      --line: #d4d4d4;
      --bg: #f7f5f1;
      --card: #fff;
      --accent: #0b3d2e;
    }}
    body {{
      margin: 0;
      font: 15px/1.45 "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    main {{
      max-width: 960px;
      margin: 0 auto;
      padding: 2rem 1.25rem 3rem;
    }}
    h1 {{
      font-size: 1.75rem;
      font-weight: 600;
      margin: 0 0 0.25rem;
      color: var(--accent);
    }}
    h2 {{
      font-size: 1.1rem;
      margin: 1.75rem 0 0.75rem;
      border-bottom: 1px solid var(--line);
      padding-bottom: 0.35rem;
    }}
    .lede {{ color: var(--muted); margin: 0 0 1.25rem; }}
    .sheets a {{ margin-right: 1rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      font-size: 0.95rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.45rem 0.6rem;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    th {{ font-weight: 600; width: 40%; }}
    tbody th {{ width: auto; }}
    .callout {{
      background: var(--card);
      border-left: 3px solid var(--accent);
      padding: 0.75rem 1rem;
      margin: 1rem 0;
    }}
  </style>
</head>
<body>
<main>
  <h1>Pool report</h1>
  <p class="lede">Run {esc(run_id)} · started {esc(run.started_at.isoformat())}</p>
  <p class="sheets">
    Sheets:
    <a href="{esc(sheet_names.get('pool_csv', ''))}">{esc(sheet_names.get('pool_csv', 'pool CSV'))}</a>
    <a href="{esc(sheet_names.get('exceptions_csv', ''))}">{esc(sheet_names.get('exceptions_csv', 'exceptions CSV'))}</a>
    <a href="{esc(sheet_names.get('position_csv', ''))}">{esc(sheet_names.get('position_csv', 'position CSV'))}</a>
    <a href="{esc(sheet_names.get('schedule_csv', ''))}">{esc(sheet_names.get('schedule_csv', 'schedule CSV'))}</a>
  </p>
  <div class="callout">
    Invalid or uncertain rows are flagged as exceptions. Share the exceptions
    sheet with Starfleet so they can correct source data and send an updated export.
    Schedule and full receivables position stay in CSV (too large for this page).
  </div>

  <section>
    <h2>Borrowing base</h2>
    <table><tbody>{metric_html}</tbody></table>
  </section>

  {delta_html}

  <section>
    <h2>Concentration (40% of AEB)</h2>
    <table>
      <thead>
        <tr><th>Sector</th><th>Eligible outstanding</th><th>Share</th><th>Excess</th><th>Within cap</th></tr>
      </thead>
      <tbody>{sector_rows or "<tr><td colspan='5'>None</td></tr>"}</tbody>
    </table>
  </section>

  <section>
    <h2>Exceptions by source</h2>
    <table>
      <thead><tr><th>Source</th><th>Count</th><th>Detail sheet</th></tr></thead>
      <tbody>{source_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Exception detail</h2>
    <p>Full machine-readable list:
      <a href="{esc(sheet_names.get('exceptions_csv', ''))}">{esc(sheet_names.get('exceptions_csv', 'exceptions CSV'))}</a>.
      Action for every row: correct source data and resend the export.
    </p>
    {more_note}
    <table>
      <thead>
        <tr><th>ID</th><th>Source</th><th>Key</th><th>Reason</th><th>See sheet</th></tr>
      </thead>
      <tbody>{exc_rows}</tbody>
    </table>
  </section>
</main>
</body>
</html>
"""
    path.write_text(body, encoding="utf-8")
