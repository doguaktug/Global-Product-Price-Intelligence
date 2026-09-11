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
from gp_price_intel.fx.service import FxService

CENTS = Decimal("0.01")

# Flat estimates are authored in one currency and converted to the caller's reference
# currency. A bare number would silently read as "450 EUR" for a user pricing in EUR.
FEE_CURRENCY = "USD"

# Courier estimate for a phone-sized parcel, per destination.
_SHIPPING_ESTIMATE: dict[str, Decimal] = {
    "TR": Decimal("14"),
    "DE": Decimal("16"),
    "GB": Decimal("15"),
    "US": Decimal("20"),
    "JP": Decimal("18"),
}
_DEFAULT_SHIPPING = Decimal("25")

# A 16-inch laptop is not a phone parcel.
_CATEGORY_SHIPPING_FACTOR: dict[str, Decimal] = {
    "smartphone": Decimal("1.0"),
    "tablet": Decimal("1.4"),
    "laptop": Decimal("2.2"),
}

_VAT_RATE: dict[str, Decimal] = {
    "TR": Decimal("0.20"),
    "DE": Decimal("0.19"),
    "GB": Decimal("0.20"),
    "US": Decimal("0.00"),
    "JP": Decimal("0.10"),
}
_DEFAULT_VAT_RATE = Decimal("0.10")

# Customs duty. Computers and tablets clear at a lower rate than handsets in several
# destinations, so the rate is looked up per (destination, category).
_DUTY_RATE: dict[str, Decimal] = {
    "TR": Decimal("0.10"),
    "DE": Decimal("0.00"),
    "GB": Decimal("0.00"),
    "US": Decimal("0.03"),
    "JP": Decimal("0.00"),
}
_DUTY_RATE_BY_CATEGORY: dict[tuple[str, str], Decimal] = {
    ("TR", "smartphone"): Decimal("0.20"),
    ("TR", "tablet"): Decimal("0.10"),
    ("TR", "laptop"): Decimal("0.00"),
    ("US", "smartphone"): Decimal("0.00"),
}
_DEFAULT_DUTY_RATE = Decimal("0.05")

# Registration levies charged on import. Türkiye registers handset IMEIs and charges
# for it; tablets and laptops are not registered.
_REGISTRATION_FEE: dict[tuple[str, str], Decimal] = {
    ("TR", "smartphone"): Decimal("1150"),
}
_REGISTRATION_LABEL: dict[tuple[str, str], str] = {
    ("TR", "smartphone"): "Estimated IMEI / TRT handset registration",
}


class LandedCostService:
    """Turn a converted list price into a destination-and-category aware total."""

    def __init__(self, fx: FxService | None = None) -> None:
        self.fx = fx or FxService()

    async def estimate(
        self,
        converted: ConvertedMoney,
        offer_country: str,
        destination_country: str,
        category_id: str,
    ) -> LandedCost:
        destination = destination_country.upper()
        origin = offer_country.upper()
        category = category_id.casefold()
        list_ref = converted.reference

        if origin == destination:
            return LandedCost(
                list_in_reference=list_ref,
                total=list_ref,
                completeness=LandedCostCompleteness.COMPLETE,
                destination_country=destination,
            )

        currency = list_ref.currency
        shipping_amount = await self._flat_fee(
            self._shipping_estimate(destination, category), currency
        )
        shipping = CostLine(
            amount=Money(amount=shipping_amount, currency=currency),
            origin=CostOrigin.ESTIMATED,
            label=f"Estimated shipping ({category})",
        )

        duty_rate = self._duty_rate(destination, category)
        duty_amount = (list_ref.amount * duty_rate).quantize(CENTS)
        import_duties = CostLine(
            amount=Money(amount=duty_amount, currency=currency),
            origin=CostOrigin.ESTIMATED,
            label=f"Estimated import duty ({duty_rate:.0%} on {category})",
        )

        vat_rate = _VAT_RATE.get(destination, _DEFAULT_VAT_RATE)
        vat_amount = (
            (list_ref.amount + shipping_amount + duty_amount) * vat_rate
        ).quantize(CENTS)
        taxes = CostLine(
            amount=Money(amount=vat_amount, currency=currency),
            origin=CostOrigin.ESTIMATED,
            label=f"Estimated VAT ({vat_rate:.0%})",
        )

        registration_amount = Decimal("0")
        registration: CostLine | None = None
        registration_fee = _REGISTRATION_FEE.get((destination, category))
        if registration_fee is not None:
            registration_amount = await self._flat_fee(registration_fee, currency)
            registration = CostLine(
                amount=Money(amount=registration_amount, currency=currency),
                origin=CostOrigin.ESTIMATED,
                label=_REGISTRATION_LABEL.get(
                    (destination, category), "Estimated registration fee"
                ),
            )

        total_amount = (
            list_ref.amount + shipping_amount + duty_amount + vat_amount + registration_amount
        )
        return LandedCost(
            list_in_reference=list_ref,
            shipping=shipping,
            taxes=taxes,
            import_duties=import_duties,
            registration_fees=registration,
            total=Money(amount=total_amount, currency=currency),
            completeness=LandedCostCompleteness.PARTIAL,
            destination_country=destination,
        )

    @staticmethod
    def _shipping_estimate(destination: str, category: str) -> Decimal:
        base = _SHIPPING_ESTIMATE.get(destination, _DEFAULT_SHIPPING)
        factor = _CATEGORY_SHIPPING_FACTOR.get(category, Decimal("1.0"))
        return base * factor

    @staticmethod
    def _duty_rate(destination: str, category: str) -> Decimal:
        override = _DUTY_RATE_BY_CATEGORY.get((destination, category))
        if override is not None:
            return override
        return _DUTY_RATE.get(destination, _DEFAULT_DUTY_RATE)

    async def _flat_fee(self, amount: Decimal, currency: str) -> Decimal:
        """Restate a fee authored in ``FEE_CURRENCY`` into the reference currency."""
        if currency.upper() == FEE_CURRENCY:
            return amount.quantize(CENTS)
        converted = await self.fx.convert(
            Money(amount=amount, currency=FEE_CURRENCY), currency
        )
        return converted.reference.amount
