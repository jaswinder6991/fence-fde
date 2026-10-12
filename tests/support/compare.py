"""Compare ground truth to pipeline report CSVs."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tests.support.ground_truth import GroundTruth


@dataclass
class CheckResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.passed.append(name)
        else:
            self.failed.append(f"{name}: {detail}" if detail else name)


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


def _parse_pool_report(path: Path) -> dict[tuple[str, str], str]:
    rows: dict[tuple[str, str], str] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            rows[(row[0], row[1])] = row[2]
    return rows


def _parse_concentration(pool_rows: dict[tuple[str, str], str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for (section, key), value in pool_rows.items():
        if section != "concentration":
            continue
        parts = {}
        for token in value.split(";"):
            if "=" not in token:
                continue
            k, v = token.split("=", 1)
            parts[k] = v
        out[key] = parts
    return out


def allocated_totals(truth: GroundTruth) -> tuple[Decimal, Decimal]:
    """Hit amounts vs installment amount_applied.

    Overpay leftover and orphan payments are absent from both sums.
    """
    from_payments = Decimal("0.00")
    on_installments = Decimal("0.00")
    for recv in truth.receivables:
        for inst in recv.installments:
            on_installments += inst.amount_applied
            for hit in inst.hits:
                from_payments += hit.amount
    return from_payments, on_installments


def check_idempotency(truth: GroundTruth) -> CheckResult:
    result = CheckResult()
    _check_idempotency(truth, result)
    return result


def _check_idempotency(truth: GroundTruth, result: CheckResult) -> None:
    result.check(
        "payments_unique_within_seen",
        truth.payment_ids_unique <= truth.payment_ids_seen,
        f"unique={truth.payment_ids_unique} seen={truth.payment_ids_seen}",
    )
    from_payments, on_installments = allocated_totals(truth)
    result.check(
        "allocated_amounts_match_applied",
        from_payments == on_installments,
        (
            f"amount_from_payment={from_payments} "
            f"amount_applied={on_installments}"
        ),
    )


def compare_to_reports(
    truth: GroundTruth,
    schedule_csv: Path,
    position_csv: Path,
    pool_csv: Path,
) -> CheckResult:
    result = CheckResult()
    _check_idempotency(truth, result)

    pool_rows = _parse_pool_report(pool_csv)
    for metric in (
        "aeb",
        "excess_concentration",
        "borrowing_base",
        "total_processed",
        "eligible_count",
        "ineligible_count",
    ):
        actual = pool_rows.get(("current", metric))
        expected = str(getattr(truth.pool, metric))
        if metric in ("total_processed", "eligible_count", "ineligible_count"):
            result.check(
                f"pool_{metric}",
                actual == expected,
                f"report={actual} truth={expected}",
            )
        else:
            try:
                match = actual is not None and _dec(actual) == _dec(expected)
            except (InvalidOperation, TypeError):
                match = False
            result.check(
                f"pool_{metric}",
                match,
                f"report={actual} truth={expected}",
            )

    conc = _parse_concentration(pool_rows)
    truth_sectors = {s.sector: s for s in truth.pool.sectors}
    result.check(
        "concentration_sector_set",
        set(conc) == set(truth_sectors),
        f"report={sorted(conc)} truth={sorted(truth_sectors)}",
    )
    for sector, s in truth_sectors.items():
        parts = conc.get(sector, {})
        try:
            eo_ok = _dec(parts.get("eligible_outstanding", "")) == s.eligible_outstanding
            ex_ok = _dec(parts.get("excess", "")) == s.excess
            cap_ok = str(parts.get("within_cap", "")).lower() == str(s.within_cap).lower()
        except (InvalidOperation, TypeError):
            eo_ok = ex_ok = cap_ok = False
        result.check(f"concentration_{sector}_outstanding", eo_ok, str(parts))
        result.check(f"concentration_{sector}_excess", ex_ok, str(parts))
        result.check(f"concentration_{sector}_within_cap", cap_ok, str(parts))

    with position_csv.open(newline="", encoding="utf-8") as f:
        position_rows = list(csv.DictReader(f))
    pos_by_id = {r["receivable_external_id"]: r for r in position_rows}
    result.check(
        "position_row_count",
        len(pos_by_id) == len(truth.receivables),
        f"report={len(pos_by_id)} truth={len(truth.receivables)}",
    )
    for recv in truth.receivables:
        row = pos_by_id.get(recv.receivable_external_id)
        if row is None:
            result.check(f"position_{recv.receivable_external_id}", False, "missing")
            continue
        result.check(
            f"position_{recv.receivable_external_id}_export",
            _dec(row["export_outstanding"]) == recv.export_outstanding,
        )
        result.check(
            f"position_{recv.receivable_external_id}_status",
            row["status"] == recv.status,
            f"report={row['status']} truth={recv.status}",
        )
        result.check(
            f"position_{recv.receivable_external_id}_eligible",
            str(row["eligible"]).lower() == str(recv.eligible).lower(),
        )
        result.check(
            f"position_{recv.receivable_external_id}_computed",
            _dec(row["computed_outstanding"]) == recv.computed_outstanding,
        )

    with schedule_csv.open(newline="", encoding="utf-8") as f:
        schedule_rows = list(csv.DictReader(f))

    truth_hits: dict[tuple[str, int, str], Decimal] = {}
    for recv in truth.receivables:
        for inst in recv.installments:
            if not inst.hits:
                truth_hits[(recv.receivable_external_id, inst.seq, "")] = Decimal("0")
            for hit in inst.hits:
                key = (recv.receivable_external_id, inst.seq, hit.payment_id)
                truth_hits[key] = truth_hits.get(key, Decimal("0")) + hit.amount

    report_hits: dict[tuple[str, int, str], Decimal] = {}
    for i, row in enumerate(schedule_rows):
        try:
            pid = row.get("payment_id") or ""
            key = (
                row["receivable_external_id"],
                int(row["installment_seq"]),
                pid,
            )
            raw_amt = row.get("amount_from_payment")
            amt = (
                _dec(raw_amt)
                if raw_amt not in (None, "")
                else Decimal("0")
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            result.check(f"schedule_row_{i}", False, str(exc))
            continue
        report_hits[key] = report_hits.get(key, Decimal("0")) + amt

    result.check(
        "schedule_hit_keys",
        set(report_hits) == set(truth_hits),
        f"only_report={sorted(set(report_hits)-set(truth_hits))[:5]} "
        f"only_truth={sorted(set(truth_hits)-set(report_hits))[:5]}",
    )
    for key, amt in truth_hits.items():
        result.check(
            f"schedule_hit_{key[0]}_{key[1]}_{key[2] or 'none'}",
            report_hits.get(key) == amt,
            f"report={report_hits.get(key)} truth={amt}",
        )

    return result


def write_truth_csvs(truth: GroundTruth, out_dir: Path) -> None:
    """Write truth in the same shapes as pipeline reports for human diff."""
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "truth_schedule.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "receivable_external_id",
            "installment_seq",
            "due_date",
            "amount_due",
            "amount_applied",
            "installment_settlement",
            "payment_id",
            "amount_from_payment",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for recv in truth.receivables:
            for inst in recv.installments:
                if not inst.hits:
                    w.writerow(
                        {
                            "receivable_external_id": recv.receivable_external_id,
                            "installment_seq": inst.seq,
                            "due_date": inst.due_date,
                            "amount_due": inst.amount_due,
                            "amount_applied": inst.amount_applied,
                            "installment_settlement": inst.settlement,
                            "payment_id": "",
                            "amount_from_payment": "",
                        }
                    )
                else:
                    for hit in inst.hits:
                        w.writerow(
                            {
                                "receivable_external_id": recv.receivable_external_id,
                                "installment_seq": inst.seq,
                                "due_date": inst.due_date,
                                "amount_due": inst.amount_due,
                                "amount_applied": inst.amount_applied,
                                "installment_settlement": inst.settlement,
                                "payment_id": hit.payment_id,
                                "amount_from_payment": hit.amount,
                            }
                        )

    with (out_dir / "truth_position.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "receivable_external_id",
            "sector",
            "face_value",
            "export_outstanding",
            "computed_outstanding",
            "days_past_due",
            "status",
            "eligible",
            "ineligibility_reasons",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for recv in truth.receivables:
            w.writerow(
                {
                    "receivable_external_id": recv.receivable_external_id,
                    "sector": recv.sector,
                    "face_value": recv.face_value,
                    "export_outstanding": recv.export_outstanding,
                    "computed_outstanding": recv.computed_outstanding,
                    "days_past_due": recv.days_past_due,
                    "status": recv.status,
                    "eligible": recv.eligible,
                    "ineligibility_reasons": "; ".join(recv.reasons),
                }
            )

    with (out_dir / "truth_pool.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["section", "key", "value"])
        p = truth.pool
        for metric in (
            "total_processed",
            "eligible_count",
            "ineligible_count",
            "aeb",
            "excess_concentration",
            "borrowing_base",
            "facility_limit",
        ):
            w.writerow(["current", metric, getattr(p, metric)])
        for s in p.sectors:
            w.writerow(
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
