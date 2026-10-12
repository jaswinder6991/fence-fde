from decimal import Decimal

from fence_pipeline.concentration import compute_pool


def test_concentration_excess_and_bb():
    pool = compute_pool(
        {
            "retail": Decimal("60000.00"),
            "logistics": Decimal("40000.00"),
        }
    )
    # AEB = 100000; 40% cap = 40000; retail excess = 20000
    assert pool.aeb == Decimal("100000.00")
    assert pool.excess_concentration == Decimal("20000.00")
    # BB = (100000 - 20000) * 0.85 = 68000
    assert pool.borrowing_base == Decimal("68000.00")
    retail = next(s for s in pool.sectors if s.sector == "retail")
    assert retail.within_cap is False
    logistics = next(s for s in pool.sectors if s.sector == "logistics")
    assert logistics.within_cap is True


def test_two_sectors_over_cap():
    pool = compute_pool(
        {
            "retail": Decimal("50000.00"),
            "logistics": Decimal("45000.00"),
            "manufacturing": Decimal("5000.00"),
        }
    )
    assert pool.aeb == Decimal("100000.00")
    # excess retail 10000 + logistics 5000 = 15000
    assert pool.excess_concentration == Decimal("15000.00")
    assert pool.borrowing_base == Decimal("72250.00")
