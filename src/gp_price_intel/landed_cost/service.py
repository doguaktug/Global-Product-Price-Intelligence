"""Landed-cost estimates after FX (shipping + import estimates)."""

from __future__ import annotations

from decimal import Decimal

from gp_price_intel.domain.models import (
    ConvertedMoney,
    CostLine,
    CostOrigin,
    LandedCost,
    LandedCostCompleteness,
    Money,
)

# Flat shipping estimate to destination (reference currency) when not quoted by source.
_SHIPPING_ESTIMATE: dict[str, Decimal] = {
    "TR": Decimal("450"),
    "DE": Decimal("15"),
    "GB": Decimal("12"),
    "US": Decimal("20"),
    "JP": Decimal("1800"),
}

# Import duty estimate as fraction of list price when cross-border.
_IMPORT_DUTY_RATE: dict[str, Decimal] = {
    "TR": Decimal("0.20"),
    "DE": Decimal("0.00"),
    "GB": Decimal("0.00"),
    "US": Decimal("0.05"),
    "JP": Decimal("0.08"),
}


class LandedCostService:
    async def estimate(
        self,
        converted: ConvertedMoney,
        offer_country: str,
        destination_country: str,
        category_id: str,
    ) -> LandedCost:
        destination = destination_country.upper()
        origin = offer_country.upper()
        list_ref = converted.reference

        if origin == destination:
            return LandedCost(
                list_in_reference=list_ref,
                total=list_ref,
                completeness=LandedCostCompleteness.COMPLETE,
                destination_country=destination,
            )

        shipping_amount = _SHIPPING_ESTIMATE.get(destination, Decimal("25"))
        shipping = CostLine(
            amount=Money(amount=shipping_amount, currency=list_ref.currency),
            origin=CostOrigin.ESTIMATED,
            label="Estimated shipping",
        )

        duty_rate = _IMPORT_DUTY_RATE.get(destination, Decimal("0.10"))
        duty_amount = (list_ref.amount * duty_rate).quantize(Decimal("0.01"))
        taxes = CostLine(
            amount=Money(amount=duty_amount, currency=list_ref.currency),
            origin=CostOrigin.ESTIMATED,
            label="Estimated import duty / VAT",
        )

        total_amount = list_ref.amount + shipping_amount + duty_amount
        return LandedCost(
            list_in_reference=list_ref,
            shipping=shipping,
            taxes=taxes,
            total=Money(amount=total_amount, currency=list_ref.currency),
            completeness=LandedCostCompleteness.PARTIAL,
            destination_country=destination,
        )
