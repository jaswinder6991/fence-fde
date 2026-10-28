"""Collections history: new vs restated vs amount-changed payments."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from common import CORRECTIONS_DIR, load_csv, money, payments_by_id, section


def run() -> dict:
    c1_rows = load_csv(CORRECTIONS_DIR / "collections-1.csv")
    c2_rows = load_csv(CORRECTIONS_DIR / "collections-2.csv")
    c1 = payments_by_id(CORRECTIONS_DIR / "collections-1.csv")
    c2 = payments_by_id(CORRECTIONS_DIR / "collections-2.csv")

    section("4. Collections membership and restatement check")
    ids1, ids2 = set(c1), set(c2)
    shared = ids1 & ids2
    new_ids = sorted(ids2 - ids1)
    dropped = sorted(ids1 - ids2)

    print(f"collections-1 payments: {len(c1)}")
    print(f"collections-2 payments: {len(c2)}")
    print(f"shared payment_ids:    {len(shared)}")
    print(f"new in export-2:       {new_ids or 'none'}")
    print(f"dropped from export-2: {dropped or 'none'}")

    restated: list[tuple[str, dict, dict]] = []
    for pid in sorted(shared):
        if c1[pid] != c2[pid]:
            restated.append((pid, c1[pid], c2[pid]))

    if restated:
        print(f"\nRESTATED / AMENDED payment rows: {len(restated)}")
        for pid, a, b in restated:
            print(f"  {pid}")
            for k in a:
                if a[k] != b[k]:
                    print(f"    {k}: {a[k]} → {b[k]}")
    else:
        print("\nNo restated payments: shared payment_ids are byte-identical across exports.")

    section("5. New payments detail + cumulative paid by receivable")
    for pid in new_ids:
        p = c2[pid]
        print(
            f"  {pid}: recv={p['receivable_external_id']} "
            f"date={p['payment_date']} amount={p['amount']}"
        )

    def totals(rows: list[dict[str, str]]) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for p in rows:
            out[p["receivable_external_id"]] += money(p["amount"])
        return dict(out)

    t1, t2 = totals(c1_rows), totals(c2_rows)
    all_recv = sorted(set(t1) | set(t2))
    print("\nCumulative paid by receivable:")
    print(f"  {'receivable':12} {'paid@1':>10} {'paid@2':>10} {'delta':>10}")
    for rid in all_recv:
        a, b = t1.get(rid, Decimal("0")), t2.get(rid, Decimal("0"))
        print(f"  {rid:12} {a:>10} {b:>10} {b - a:>10}")

    return {
        "new_ids": new_ids,
        "dropped": dropped,
        "restated": restated,
        "paid_at_1": t1,
        "paid_at_2": t2,
    }


if __name__ == "__main__":
    run()
