from decimal import Decimal

from fence_pipeline.eligibility import evaluate_eligibility
from fence_pipeline.status import (
    STATUS_CLOSED,
    STATUS_CURRENT,
    STATUS_DEFAULTED,
    STATUS_DELINQUENT,
    derive_status,
)


def test_status_order():
    assert derive_status(Decimal("0"), 100) == STATUS_DEFAULTED
    assert derive_status(Decimal("0"), 90) == STATUS_DEFAULTED
    assert derive_status(Decimal("0"), 0) == STATUS_CLOSED
    assert derive_status(Decimal("0"), 45) == STATUS_CLOSED
    assert derive_status(Decimal("10"), 90) == STATUS_DEFAULTED
    assert derive_status(Decimal("10"), 30) == STATUS_DELINQUENT
    assert derive_status(Decimal("10"), 0) == STATUS_CURRENT


def test_eligibility_pass():
    d = evaluate_eligibility(
        face_value=Decimal("50000"),
        obligor_risk_score=70,
        sector="retail",
        currency="USD",
        outstanding_balance=Decimal("1000"),
        days_past_due=0,
        obligor_has_other_defaulted=False,
    )
    assert d.eligible
    assert d.reasons == []


def test_eligibility_failures():
    d = evaluate_eligibility(
        face_value=Decimal("150000"),
        obligor_risk_score=50,
        sector="automotive",
        currency="EUR",
        outstanding_balance=Decimal("1000"),
        days_past_due=95,
        obligor_has_other_defaulted=True,
    )
    assert not d.eligible
    assert any("face_value" in r for r in d.reasons)
    assert any("obligor_risk_score" in r for r in d.reasons)
    assert any("sector" in r for r in d.reasons)
    assert any("currency" in r for r in d.reasons)
    assert any("Defaulted" in r for r in d.reasons)
    assert any("same obligor" in r for r in d.reasons)
