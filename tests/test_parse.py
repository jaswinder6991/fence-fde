from datetime import date
from decimal import Decimal

import pytest

from fence_pipeline.parse import (
    ParseError,
    add_months,
    normalize_sector,
    parse_date,
    parse_receivable_row,
)


def test_parse_date_ymd_and_dmy():
    assert parse_date("2025-03-10") == date(2025, 3, 10)
    assert parse_date("26/05/2025") == date(2025, 5, 26)


def test_parse_date_invalid():
    with pytest.raises(ParseError):
        parse_date("not-a-date")


def test_sector_normalize_whitespace_and_case():
    raw, sector = normalize_sector(" Retail ")
    assert raw == "Retail"
    assert sector == "retail"
    raw2, sector2 = normalize_sector("RETAIL")
    assert sector2 == "retail"


def test_add_months_eom_clamp():
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 3, 31), 1) == date(2025, 4, 30)


def test_parse_receivable_mixed_date():
    row = {
        "receivable_external_id": "SL-1",
        "merchant_name": "Shop",
        "obligor_name": "Obligor",
        "sector": "Logistics",
        "purchase_date": "14/03/2026",
        "face_value": "5000",
        "purchase_price": "4900",
        "number_of_installments": "2",
        "currency": "USD",
        "obligor_risk_score": "90",
        "outstanding_balance": "0.00",
        "days_past_due": "0",
    }
    parsed = parse_receivable_row(row)
    assert parsed.purchase_date == date(2026, 3, 14)
    assert parsed.sector == "logistics"
    assert parsed.face_value == Decimal("5000.00")
