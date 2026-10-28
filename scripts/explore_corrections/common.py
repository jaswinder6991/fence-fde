"""Shared loaders for corrections CSV exploration (no DB, no pipeline writes)."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORRECTIONS_DIR = ROOT / "data" / "corrections"

SCHEDULE_FIELDS = ("face_value", "number_of_installments", "purchase_date")
POSITION_FIELDS = ("outstanding_balance", "days_past_due")
ATTRIBUTE_FIELDS = (
    "merchant_name",
    "obligor_name",
    "sector",
    "purchase_price",
    "currency",
    "obligor_risk_score",
)


@dataclass(frozen=True)
class InstallmentSpec:
    seq: int
    due_date: date
    amount_due: Decimal


def add_months(base: date, months: int) -> date:
    """Mirror fence_pipeline.parse.add_months (anchor day, clamp target month)."""
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    day = min(base.day, last_day)
    return date(year, month, day)


def money_to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value(rounding=ROUND_DOWN))


def cents_to_money(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


def build_schedule(
    purchase_date: date,
    face_value: Decimal,
    number_of_installments: int,
) -> list[InstallmentSpec]:
    """Section 5 equal installments; remainder cents on the last installment."""
    if number_of_installments < 1:
        raise ValueError("number_of_installments must be >= 1")
    total_cents = money_to_cents(face_value)
    base = total_cents // number_of_installments
    remainder = total_cents - base * number_of_installments
    specs: list[InstallmentSpec] = []
    for i in range(1, number_of_installments + 1):
        cents = base + (remainder if i == number_of_installments else 0)
        specs.append(
            InstallmentSpec(
                seq=i,
                due_date=add_months(purchase_date, i),
                amount_due=cents_to_money(cents),
            )
        )
    return specs


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def money(value: str) -> Decimal:
    return Decimal(value)


def receivables_by_id(path: Path) -> dict[str, dict[str, str]]:
    rows = load_csv(path)
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        rid = row["receivable_external_id"]
        if rid in out:
            raise ValueError(f"duplicate receivable_external_id in {path.name}: {rid}")
        out[rid] = row
    return out


def payments_by_id(path: Path) -> dict[str, dict[str, str]]:
    rows = load_csv(path)
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        pid = row["payment_id"]
        if pid in out:
            raise ValueError(f"duplicate payment_id in {path.name}: {pid}")
        out[pid] = row
    return out


def paid_by_receivable(payments: list[dict[str, str]]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for p in payments:
        totals[p["receivable_external_id"]] += money(p["amount"])
    return dict(totals)


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
