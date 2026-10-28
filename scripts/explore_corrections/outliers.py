"""Cases the PDF rule does not cover — catalog for product decisions."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from common import (
    CORRECTIONS_DIR,
    build_schedule,
    load_csv,
    money,
    paid_by_receivable,
    parse_iso_date,
    receivables_by_id,
    section,
)


def run() -> list[str]:
    r1 = receivables_by_id(CORRECTIONS_DIR / "receivables-1.csv")
    r2 = receivables_by_id(CORRECTIONS_DIR / "receivables-2.csv")
    c1 = load_csv(CORRECTIONS_DIR / "collections-1.csv")
    c2 = load_csv(CORRECTIONS_DIR / "collections-2.csv")
    paid1 = paid_by_receivable(c1)
    paid2 = paid_by_receivable(c2)

    findings: list[str] = []

    def note(msg: str) -> None:
        findings.append(msg)
        print(f"  • {msg}")

    section("9. Outliers / open decisions beyond face_value + N")

    # --- face increases / N increases (none expected in this fixture) ---
    print("\n[face / installment-count direction]")
    for rid in sorted(set(r1) & set(r2)):
        a, b = r1[rid], r2[rid]
        fv1, fv2 = money(a["face_value"]), money(b["face_value"])
        n1, n2 = int(a["number_of_installments"]), int(b["number_of_installments"])
        if fv2 > fv1:
            note(f"{rid}: face INCREASED {fv1} → {fv2} (PDF example is cancel/decrease)")
        if n2 > n1:
            note(f"{rid}: installment count INCREASED {n1} → {n2}")
        if fv2 < fv1 and n2 == n1:
            note(
                f"{rid}: face decreased with SAME N "
                f"({n1}) — rebuild equal installments on new face"
            )
        if fv2 != fv1 and n2 != n1:
            note(
                f"{rid}: face AND N both changed "
                f"(face {fv1}→{fv2}, N {n1}→{n2})"
            )
        if fv2 == fv1 and n2 != n1:
            note(f"{rid}: N changed with face unchanged ({n1}→{n2})")

    # --- purchase_date / other immutables ---
    print("\n[fields outside the PDF trigger]")
    for rid in sorted(set(r1) & set(r2)):
        a, b = r1[rid], r2[rid]
        for field in (
            "purchase_date",
            "merchant_name",
            "obligor_name",
            "sector",
            "currency",
            "obligor_risk_score",
        ):
            if a[field] != b[field]:
                note(f"{rid}: {field} changed {a[field]!r} → {b[field]!r}")
        # purchase_price should track discount; flag if not 0.99 * face
        for label, row in (("export-1", a), ("export-2", b)):
            fv, pp = money(row["face_value"]), money(row["purchase_price"])
            expected = (fv * Decimal("0.99")).quantize(Decimal("0.01"))
            if pp != expected:
                note(
                    f"{rid} {label}: purchase_price {pp} != 0.99*face "
                    f"(expected {expected})"
                )

    # --- overpay vs corrected face ---
    print("\n[cash vs corrected face]")
    for rid in sorted(set(r1) & set(r2)):
        a, b = r1[rid], r2[rid]
        if a["face_value"] == b["face_value"] and a["number_of_installments"] == b[
            "number_of_installments"
        ]:
            continue
        fv2 = money(b["face_value"])
        paid = paid2.get(rid, Decimal("0.00"))
        ob2 = money(b["outstanding_balance"])
        if paid > fv2:
            note(
                f"{rid}: paid@2 ({paid}) > new face ({fv2}) by {paid - fv2}; "
                f"declared OB={ob2} — leftover cash / Closed / exception?"
            )
        if money(b["face_value"]) - paid != ob2 and paid <= fv2:
            note(
                f"{rid}: declared OB {ob2} != new_face−paid "
                f"({fv2 - paid}) after correction"
            )

    # --- DPD moves without new payments ---
    print("\n[DPD / delinquency without matching cash activity]")
    paid_delta = {
        rid: paid2.get(rid, Decimal("0")) - paid1.get(rid, Decimal("0"))
        for rid in set(r1) | set(r2)
    }
    for rid in sorted(set(r1) & set(r2)):
        a, b = r1[rid], r2[rid]
        dpd1, dpd2 = int(a["days_past_due"]), int(b["days_past_due"])
        if dpd1 == dpd2:
            continue
        delta_paid = paid_delta.get(rid, Decimal("0"))
        note(
            f"{rid}: DPD {dpd1}→{dpd2} (Δ{dpd2 - dpd1}); "
            f"paid delta between exports={delta_paid}"
        )
        # Rough check: does DPD look consistent with earliest unpaid due date?
        # Use export-2 terms + paid@2 waterfall mentally via schedule amounts.
        specs = build_schedule(
            parse_iso_date(b["purchase_date"]),
            money(b["face_value"]),
            int(b["number_of_installments"]),
        )
        remaining = paid2.get(rid, Decimal("0.00"))
        first_unpaid_due = None
        for s in specs:
            if remaining >= s.amount_due:
                remaining -= s.amount_due
            else:
                first_unpaid_due = s.due_date
                break
        if first_unpaid_due is None:
            note(
                f"{rid}: under NEW schedule + paid@2, all installments covered "
                f"(or overpaid) — DPD {dpd2} may be stale vs schedule"
            )
        else:
            # Implied as-of if DPD is exact days past that due date
            implied_asof = first_unpaid_due + timedelta(days=dpd2)
            note(
                f"{rid}: under NEW schedule, first unpaid due={first_unpaid_due}; "
                f"if DPD={dpd2} is exact, implied as-of ≈ {implied_asof}"
            )

    # --- payments referencing unknown receivables ---
    print("\n[orphan / unknown receivable payments]")
    known = set(r2)  # latest book
    for p in c2:
        if p["receivable_external_id"] not in known:
            note(
                f"payment {p['payment_id']} targets "
                f"{p['receivable_external_id']} absent from receivables-2"
            )

    # --- duplicate payment dates / multiple payments same day ---
    print("\n[payment shape]")
    by_recv_date: dict[tuple[str, str], list[str]] = defaultdict(list)
    for p in c2:
        by_recv_date[(p["receivable_external_id"], p["payment_date"])].append(
            p["payment_id"]
        )
    for (rid, d), pids in sorted(by_recv_date.items()):
        if len(pids) > 1:
            note(f"{rid} on {d}: multiple payments {pids}")

    # --- position-only receivables (not PDF corrections) ---
    print("\n[position-only — not a PDF correction, must not rebuild schedule]")
    for rid in sorted(set(r1) & set(r2)):
        a, b = r1[rid], r2[rid]
        schedule_same = (
            a["face_value"] == b["face_value"]
            and a["number_of_installments"] == b["number_of_installments"]
            and a["purchase_date"] == b["purchase_date"]
        )
        position_changed = (
            a["outstanding_balance"] != b["outstanding_balance"]
            or a["days_past_due"] != b["days_past_due"]
        )
        if schedule_same and position_changed:
            note(
                f"{rid}: position-only update "
                f"OB {a['outstanding_balance']}→{b['outstanding_balance']}, "
                f"DPD {a['days_past_due']}→{b['days_past_due']} "
                f"(paid Δ={paid_delta.get(rid, 0)}) — keep existing schedule"
            )

    # --- dropped / new receivables between full snapshots ---
    print("\n[portfolio membership]")
    for rid in sorted(set(r1) - set(r2)):
        note(f"{rid}: present in export-1, absent in export-2 (inactivation path)")
    for rid in sorted(set(r2) - set(r1)):
        note(f"{rid}: new in export-2 only")

    if not findings:
        print("\n  (no outlier notes — unexpected for this fixture)")

    section("10. Outlier catalog summary")
    print(f"Total findings: {len(findings)}")
    for i, msg in enumerate(findings, 1):
        print(f"  {i:2}. {msg}")

    return findings


if __name__ == "__main__":
    run()
