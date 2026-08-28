"""Landed-cost layer stub (shipping, tax, duty after FX)."""

from __future__ import annotations

from gp_price_intel.domain.models import ConvertedMoney, LandedCost, LandedCostCompleteness


class LandedCostService:
    async def estimate(
        self,
        converted: ConvertedMoney,
        offer_country: str,
        destination_country: str,
        category_id: str,
    ) -> LandedCost:
        # Week 2: quote shipping when available; otherwise labeled estimates.
        return LandedCost(
            list_in_reference=converted.reference,
            total=converted.reference,
            completeness=LandedCostCompleteness.PARTIAL,
            destination_country=destination_country,
        )
