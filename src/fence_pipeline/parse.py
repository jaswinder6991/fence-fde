"""Parsing helpers for export CSVs: dates, money, sector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN

ELIGIBLE_SECTORS = frozenset(
    {"logistics", "manufacturing", "retail", "hospitality"}
)

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")


class ParseError(ValueError):
    """Row-level parse failure with a specific reason."""


def parse_date(value: str | None) -> date:
    raw = (value or "").strip()
    if not raw:
        raise ParseError("missing date")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"unparseable date: {raw!r}")


def parse_money(value: str | None, field: str) -> Decimal:
    raw = (value or "").strip()
    if not raw:
        raise ParseError(f"missing {field}")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ParseError(f"invalid {field}: {raw!r}") from exc
    if amount < 0:
        raise ParseError(f"negative {field}: {raw!r}")
    return amount.quantize(Decimal("0.01"))


def parse_int(value: str | None, field: str, *, min_value: int | None = None) -> int:
    raw = (value or "").strip()
    if not raw:
        raise ParseError(f"missing {field}")
    try:
        number = int(raw)
    except ValueError as exc:
        raise ParseError(f"invalid {field}: {raw!r}") from exc
    if min_value is not None and number < min_value:
        raise ParseError(f"{field} below minimum {min_value}: {number}")
    return number


def normalize_sector(value: str | None) -> tuple[str, str]:
    """Return (raw_stripped, canonical_casefold). Blank sector is a parse error."""
    raw = (value or "").strip()
    if not raw:
        raise ParseError("missing sector")
    return raw, raw.casefold()


def is_eligible_sector(sector: str) -> bool:
    return sector in ELIGIBLE_SECTORS


def money_to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value(rounding=ROUND_DOWN))


def cents_to_money(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class ParsedReceivable:
    receivable_external_id: str
    merchant_name: str
    obligor_name: str
    sector_raw: str
    sector: str
    purchase_date: date
    face_value: Decimal
    purchase_price: Decimal
    number_of_installments: int
    currency: str
    obligor_risk_score: int
    outstanding_balance: Decimal
    days_past_due: int


@dataclass(frozen=True)
class ParsedPayment:
    payment_id: str
    receivable_external_id: str
    payment_date: date
    amount: Decimal


def parse_receivable_row(row: dict[str, str]) -> ParsedReceivable:
    ext_id = (row.get("receivable_external_id") or "").strip()
    if not ext_id:
        raise ParseError("missing receivable_external_id")

    merchant = (row.get("merchant_name") or "").strip()
    if not merchant:
        raise ParseError("missing merchant_name")

    obligor = (row.get("obligor_name") or "").strip()
    if not obligor:
        raise ParseError("missing obligor_name")

    sector_raw, sector = normalize_sector(row.get("sector"))
    purchase_date = parse_date(row.get("purchase_date"))
    face_value = parse_money(row.get("face_value"), "face_value")
    purchase_price = parse_money(row.get("purchase_price"), "purchase_price")
    n_inst = parse_int(
        row.get("number_of_installments"), "number_of_installments", min_value=1
    )
    currency = (row.get("currency") or "").strip()
    if not currency:
        raise ParseError("missing currency")
    score = parse_int(row.get("obligor_risk_score"), "obligor_risk_score")
    if score < 0 or score > 100:
        raise ParseError(f"obligor_risk_score out of range: {score}")
    outstanding = parse_money(row.get("outstanding_balance"), "outstanding_balance")
    dpd = parse_int(row.get("days_past_due"), "days_past_due", min_value=0)

    return ParsedReceivable(
        receivable_external_id=ext_id,
        merchant_name=merchant,
        obligor_name=obligor,
        sector_raw=sector_raw,
        sector=sector,
        purchase_date=purchase_date,
        face_value=face_value,
        purchase_price=purchase_price,
        number_of_installments=n_inst,
        currency=currency,
        obligor_risk_score=score,
        outstanding_balance=outstanding,
        days_past_due=dpd,
    )


def parse_payment_row(row: dict[str, str]) -> ParsedPayment:
    payment_id = (row.get("payment_id") or "").strip()
    if not payment_id:
        raise ParseError("missing payment_id")
    ext_id = (row.get("receivable_external_id") or "").strip()
    if not ext_id:
        raise ParseError("missing receivable_external_id")
    payment_date = parse_date(row.get("payment_date"))
    amount = parse_money(row.get("amount"), "amount")
    if amount == 0:
        raise ParseError("zero payment amount")
    return ParsedPayment(
        payment_id=payment_id,
        receivable_external_id=ext_id,
        payment_date=payment_date,
        amount=amount,
    )


def add_months(base: date, months: int) -> date:
    """Add calendar months from ``base``, clamping only in the target month.

    The day-of-month anchor stays on ``base.day``. A 31 January start becomes
    28/29 February, then 31 March, then 30 April — it does not chain from the
    clamped February date (which would wrongly drift to 28 March).
    """
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    day = min(base.day, last_day)
    return date(year, month, day)
