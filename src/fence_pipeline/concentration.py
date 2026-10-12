"""Sector concentration and borrowing base."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from collections import defaultdict

ADVANCE_RATE = Decimal("0.85")
SECTOR_CAP = Decimal("0.40")
FACILITY_LIMIT = Decimal("250000000")


@dataclass(frozen=True)
class SectorResult:
    sector: str
    eligible_outstanding: Decimal
    share: Decimal
    excess: Decimal
    within_cap: bool


@dataclass(frozen=True)
class PoolResult:
    aeb: Decimal
    excess_concentration: Decimal
    borrowing_base: Decimal
    facility_limit: Decimal
    sectors: list[SectorResult]


def compute_pool(
    eligible_by_sector: dict[str, Decimal],
) -> PoolResult:
    aeb = sum(eligible_by_sector.values(), Decimal("0")).quantize(Decimal("0.01"))
    sectors: list[SectorResult] = []
    total_excess = Decimal("0.00")

    if aeb == 0:
        for sector, bal in sorted(eligible_by_sector.items()):
            sectors.append(
                SectorResult(
                    sector=sector,
                    eligible_outstanding=bal,
                    share=Decimal("0"),
                    excess=Decimal("0.00"),
                    within_cap=True,
                )
            )
        return PoolResult(
            aeb=aeb,
            excess_concentration=Decimal("0.00"),
            borrowing_base=Decimal("0.00"),
            facility_limit=FACILITY_LIMIT,
            sectors=sectors,
        )

    cap_amount = (aeb * SECTOR_CAP).quantize(Decimal("0.01"))
    for sector, bal in sorted(eligible_by_sector.items()):
        share = (bal / aeb).quantize(Decimal("0.00000001"))
        excess = max(Decimal("0.00"), bal - cap_amount)
        total_excess += excess
        sectors.append(
            SectorResult(
                sector=sector,
                eligible_outstanding=bal,
                share=share,
                excess=excess,
                within_cap=excess == 0,
            )
        )

    total_excess = total_excess.quantize(Decimal("0.01"))
    adjusted = aeb - total_excess
    bb = (adjusted * ADVANCE_RATE).quantize(Decimal("0.01"))
    return PoolResult(
        aeb=aeb,
        excess_concentration=total_excess,
        borrowing_base=bb,
        facility_limit=FACILITY_LIMIT,
        sectors=sectors,
    )


def aggregate_eligible(
    rows: list[tuple[str, Decimal, bool]],
) -> dict[str, Decimal]:
    """rows: (sector, outstanding_balance, eligible)."""
    by_sector: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for sector, outstanding, eligible in rows:
        if eligible:
            by_sector[sector] += outstanding
    return {k: v.quantize(Decimal("0.01")) for k, v in by_sector.items()}
