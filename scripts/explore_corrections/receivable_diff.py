"""Field-level receivable snapshot diff; classify correction vs position-only."""

from __future__ import annotations

from decimal import Decimal

from common import (
    ATTRIBUTE_FIELDS,
    CORRECTIONS_DIR,
    POSITION_FIELDS,
    SCHEDULE_FIELDS,
    money,
    receivables_by_id,
    section,
)


def classify_change(before: dict[str, str], after: dict[str, str]) -> dict:
    changed = {
        k: (before[k], after[k])
        for k in before
        if k in after and before[k] != after[k]
    }
    schedule = {k: changed[k] for k in SCHEDULE_FIELDS if k in changed}
    position = {k: changed[k] for k in POSITION_FIELDS if k in changed}
    attributes = {k: changed[k] for k in ATTRIBUTE_FIELDS if k in changed}
    other = {
        k: v
        for k, v in changed.items()
        if k not in SCHEDULE_FIELDS + POSITION_FIELDS + ATTRIBUTE_FIELDS
    }

    # PDF "correction" trigger: face_value or number_of_installments only.
    pdf_correction = (
        "face_value" in schedule or "number_of_installments" in schedule
    )
    purchase_date_changed = "purchase_date" in schedule

    return {
        "changed": changed,
        "schedule": schedule,
        "position": position,
        "attributes": attributes,
        "other": other,
        "pdf_correction": pdf_correction,
        "purchase_date_changed": purchase_date_changed,
        "position_only": bool(position) and not schedule and not attributes and not other,
    }


def run() -> list[dict]:
    r1 = receivables_by_id(CORRECTIONS_DIR / "receivables-1.csv")
    r2 = receivables_by_id(CORRECTIONS_DIR / "receivables-2.csv")

    section("1. Receivable snapshot membership")
    only_1 = sorted(set(r1) - set(r2))
    only_2 = sorted(set(r2) - set(r1))
    both = sorted(set(r1) & set(r2))
    print(f"export-1 count: {len(r1)}")
    print(f"export-2 count: {len(r2)}")
    print(f"in both:        {len(both)}")
    print(f"dropped (1→2):  {only_1 or 'none'}")
    print(f"new (2 only):   {only_2 or 'none'}")

    section("2. Per-receivable field changes (1 → 2)")
    rows: list[dict] = []
    for rid in both:
        info = classify_change(r1[rid], r2[rid])
        info["receivable_external_id"] = rid
        info["before"] = r1[rid]
        info["after"] = r2[rid]
        rows.append(info)

        tag = (
            "PDF_CORRECTION"
            if info["pdf_correction"]
            else (
                "POSITION_ONLY"
                if info["changed"] and not info["schedule"]
                else ("UNCHANGED" if not info["changed"] else "OTHER_CHANGE")
            )
        )
        print(f"\n{rid}  [{tag}]")
        if not info["changed"]:
            print("  (no field changes)")
            continue
        for field, (a, b) in info["changed"].items():
            kind = (
                "schedule"
                if field in SCHEDULE_FIELDS
                else "position"
                if field in POSITION_FIELDS
                else "attribute"
                if field in ATTRIBUTE_FIELDS
                else "other"
            )
            print(f"  {field:24} {a:>12} → {b:<12}  ({kind})")

        # purchase_price vs face consistency when either moves
        for label, row in (("before", r1[rid]), ("after", r2[rid])):
            fv, pp = money(row["face_value"]), money(row["purchase_price"])
            ratio = (pp / fv) if fv else Decimal("NaN")
            print(f"  purchase_price/face ({label}): {ratio}")

    corrections = [r for r in rows if r["pdf_correction"]]
    position_only = [
        r for r in rows if r["changed"] and not r["pdf_correction"] and not r["schedule"]
    ]
    purchase_date = [r for r in rows if r["purchase_date_changed"]]

    section("3. Classification summary")
    print(f"PDF corrections (face and/or N changed): {len(corrections)}")
    for r in corrections:
        sch = r["schedule"]
        bits = ", ".join(f"{k}: {a}→{b}" for k, (a, b) in sch.items())
        print(f"  - {r['receivable_external_id']}: {bits}")
    print(f"Position-only updates (no schedule fields): {len(position_only)}")
    for r in position_only:
        bits = ", ".join(f"{k}: {a}→{b}" for k, (a, b) in r["position"].items())
        print(f"  - {r['receivable_external_id']}: {bits}")
    print(f"purchase_date changes: {len(purchase_date)}")
    for r in purchase_date:
        a, b = r["schedule"]["purchase_date"]
        print(f"  - {r['receivable_external_id']}: {a} → {b}")

    return rows


if __name__ == "__main__":
    run()
