"""Section 5 schedules under old vs new terms, plus cash vs declared OB."""

from __future__ import annotations

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


def schedule_lines(row: dict[str, str]) -> list[str]:
    specs = build_schedule(
        parse_iso_date(row["purchase_date"]),
        money(row["face_value"]),
        int(row["number_of_installments"]),
    )
    return [
        f"seq={s.seq} due={s.due_date} amount_due={s.amount_due}" for s in specs
    ]


def run() -> list[dict]:
    r1 = receivables_by_id(CORRECTIONS_DIR / "receivables-1.csv")
    r2 = receivables_by_id(CORRECTIONS_DIR / "receivables-2.csv")
    paid1 = paid_by_receivable(load_csv(CORRECTIONS_DIR / "collections-1.csv"))
    paid2 = paid_by_receivable(load_csv(CORRECTIONS_DIR / "collections-2.csv"))

    section("6. Expected schedules (Section 5) before vs after")
    evidence: list[dict] = []
    for rid in sorted(set(r1) & set(r2)):
        a, b = r1[rid], r2[rid]
        face_changed = a["face_value"] != b["face_value"]
        n_changed = a["number_of_installments"] != b["number_of_installments"]
        if not (face_changed or n_changed):
            print(f"\n{rid}: terms unchanged — skip schedule rebuild comparison")
            continue

        before = schedule_lines(a)
        after = schedule_lines(b)
        print(f"\n{rid}")
        print(
            f"  terms: face {a['face_value']}→{b['face_value']}, "
            f"N {a['number_of_installments']}→{b['number_of_installments']}, "
            f"purchase_date={a['purchase_date']}"
        )
        print("  schedule BEFORE:")
        for line in before:
            print(f"    {line}")
        print("  schedule AFTER:")
        for line in after:
            print(f"    {line}")

        evidence.append(
            {
                "receivable_external_id": rid,
                "before_lines": before,
                "after_lines": after,
            }
        )

    section("7. Cash accounting: face − paid vs declared outstanding")
    print(
        "For each receivable at each export: "
        "platform_ob = face − cumulative_paid_in_that_export; "
        "compare to declared outstanding_balance."
    )
    print(
        f"\n  {'recv':8} {'exp':4} {'face':>8} {'paid':>8} "
        f"{'face-paid':>10} {'decl_OB':>8} {'match':>6} {'overpay':>7}"
    )

    for exp_label, recv_map, paid_map in (
        ("1", r1, paid1),
        ("2", r2, paid2),
    ):
        for rid in sorted(recv_map):
            row = recv_map[rid]
            face = money(row["face_value"])
            paid = paid_map.get(rid, Decimal("0.00"))
            computed = face - paid
            declared = money(row["outstanding_balance"])
            match = computed == declared
            overpay = paid > face
            print(
                f"  {rid:8} {exp_label:4} {face:>8} {paid:>8} "
                f"{computed:>10} {declared:>8} "
                f"{'YES' if match else 'NO':>6} "
                f"{'YES' if overpay else '':>7}"
            )

    section("8. Correction-window cash story (paid@2 vs old/new face)")
    print(
        "After export-2, how paid cash sits relative to the old face "
        "(what was owed) vs the corrected face (what is owed now)."
    )
    for rid in sorted(set(r1) & set(r2)):
        a, b = r1[rid], r2[rid]
        if a["face_value"] == b["face_value"] and a["number_of_installments"] == b[
            "number_of_installments"
        ]:
            continue
        fv1, fv2 = money(a["face_value"]), money(b["face_value"])
        paid = paid2.get(rid, Decimal("0.00"))
        ob2 = money(b["outstanding_balance"])
        print(f"\n{rid}")
        print(f"  paid@2={paid}  old_face={fv1}  new_face={fv2}  decl_OB@2={ob2}")
        print(f"  new_face − paid = {fv2 - paid}  (declared match: {fv2 - paid == ob2})")
        if paid > fv2:
            print(
                f"  OUTLIER: overpay vs corrected face by {paid - fv2} "
                f"(declared OB is {ob2})"
            )
        if paid > fv1:
            print(f"  OUTLIER: paid also exceeds original face by {paid - fv1}")

    return evidence


if __name__ == "__main__":
    run()
