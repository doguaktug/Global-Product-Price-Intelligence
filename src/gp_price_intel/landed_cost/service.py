"""Landed-cost estimates after FX (shipping + import estimates)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from gp_price_intel.config import get_settings
from gp_price_intel.domain.models import (
    ConvertedMoney,
    CostLine,
    CostOrigin,
    LandedCost,
    LandedCostCompleteness,
    Money,
)
from gp_price_intel.fx.service import FxService

logger = logging.getLogger(__name__)

CENTS = Decimal("0.01")

# Flat estimates are authored in one currency and converted to the caller's reference
# currency. A bare number would silently read as "450 EUR" for a user pricing in EUR.
FEE_CURRENCY = "USD"

_SHIPPING_LANES_FILE = "shipping_lanes.json"

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


@dataclass(frozen=True)
class _ShippingEstimate:
    amount: Decimal
    #: False when no lane was published and a generic figure was used instead. That is
    #: a guess about the single largest add-on, so it downgrades the whole estimate.
    from_published_lane: bool


class ShippingLanes:
    """
    Courier estimates per origin→destination lane, loaded from fixtures.

    Shipping depends on *both* ends of the journey — DE→TR is a short regional hop
    while JP→TR is long-haul — so keying the estimate on the destination alone
    understated cheap lanes and overstated expensive ones by the same amount.

    These are curated fixtures, not quotes. Replacing them with a carrier rate API
    (or per-source published shipping tables) is the intended next step; see
    docs/data-source-strategy.md.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_settings().data_dir / "fixtures" / _SHIPPING_LANES_FILE)
        payload: dict[str, Any] = {}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            logger.warning("Shipping lane fixtures missing at %s", self.path)

        self.fee_currency = str(payload.get("fee_currency", FEE_CURRENCY))
        self.lanes = {
            str(key).upper(): Decimal(str(value))
            for key, value in (payload.get("lanes") or {}).items()
        }
        self._domestic = _optional_decimal(payload.get("domestic"))
        self._default_cross_border = _optional_decimal(payload.get("default_cross_border"))

    def estimate(self, origin: str, destination: str, category: str) -> _ShippingEstimate | None:
        """None when nothing is published and no fallback exists — an unknown fee."""
        factor = _CATEGORY_SHIPPING_FACTOR.get(category, Decimal("1.0"))
        lane = self.lanes.get(f"{origin}-{destination}")
        if lane is not None:
            return _ShippingEstimate(lane * factor, from_published_lane=True)

        fallback = self._domestic if origin == destination else self._default_cross_border
        if fallback is None:
            return None
        return _ShippingEstimate(fallback * factor, from_published_lane=False)


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class LandedCostService:
    """Turn a converted list price into an origin-, destination- and category-aware total."""

    def __init__(
        self,
        fx: FxService | None = None,
        shipping_lanes: ShippingLanes | None = None,
    ) -> None:
        self.fx = fx or FxService()
        self.shipping_lanes = shipping_lanes or ShippingLanes()

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

        currency = list_ref.currency

        if origin == destination:
            # Buying at home: the sticker already includes local tax and there is no
            # border to cross. Domestic shipping is the only add-on, and it is the one
            # figure a retailer routinely states, so this stays the honest "complete".
            domestic = self.shipping_lanes.estimate(origin, destination, category)
            if domestic is None:
                return LandedCost(
                    list_in_reference=list_ref,
                    total=list_ref,
                    completeness=LandedCostCompleteness.COMPLETE,
                    destination_country=destination,
                )
            amount = await self._flat_fee(domestic.amount, currency)
            return LandedCost(
                list_in_reference=list_ref,
                shipping=CostLine(
                    amount=Money(amount=amount, currency=currency),
                    origin=CostOrigin.ESTIMATED,
                    label=f"Estimated domestic shipping ({category})",
                ),
                total=Money(amount=list_ref.amount + amount, currency=currency),
                completeness=LandedCostCompleteness.COMPLETE
                if domestic.from_published_lane
                else LandedCostCompleteness.PARTIAL,
                destination_country=destination,
            )

        other_fees: list[CostLine] = []
        unknown_lines: list[str] = []
        guessed_lines: list[str] = []

        # Origin VAT comes off before anything else. A €1,349 German sticker includes
        # 19% German VAT, which an export sale does not charge; adding Turkish VAT on
        # top of it would tax the buyer twice and overstate every import.
        origin_vat_rate = _VAT_RATE.get(origin)
        if origin_vat_rate is None:
            taxable_base = list_ref.amount
            unknown_lines.append(f"origin VAT rate for {origin}")
        else:
            taxable_base = (list_ref.amount / (Decimal("1") + origin_vat_rate)).quantize(CENTS)
            refunded = (taxable_base - list_ref.amount).quantize(CENTS)
            if refunded:
                other_fees.append(
                    CostLine(
                        amount=Money(amount=refunded, currency=currency),
                        origin=CostOrigin.ESTIMATED,
                        label=(
                            f"{origin} VAT removed on export ({origin_vat_rate:.0%}) — "
                            "not charged on a cross-border sale"
                        ),
                    )
                )

        shipping_estimate = self.shipping_lanes.estimate(origin, destination, category)
        if shipping_estimate is None:
            shipping_amount = Decimal("0")
            shipping = CostLine(
                amount=Money(amount=shipping_amount, currency=currency),
                origin=CostOrigin.UNAVAILABLE,
                label=f"Shipping {origin}→{destination} could not be estimated",
            )
            unknown_lines.append(f"shipping on the {origin}→{destination} lane")
        else:
            shipping_amount = await self._flat_fee(shipping_estimate.amount, currency)
            shipping = CostLine(
                amount=Money(amount=shipping_amount, currency=currency),
                origin=CostOrigin.ESTIMATED,
                label=f"Estimated shipping {origin}→{destination} ({category})",
            )
            if not shipping_estimate.from_published_lane:
                guessed_lines.append(f"shipping on the {origin}→{destination} lane")

        duty_rate, duty_is_published = self._duty_rate(destination, category)
        duty_amount = (taxable_base * duty_rate).quantize(CENTS)
        import_duties = CostLine(
            amount=Money(amount=duty_amount, currency=currency),
            origin=CostOrigin.ESTIMATED if duty_is_published else CostOrigin.UNAVAILABLE,
            label=f"Estimated import duty ({duty_rate:.0%} on {category})",
        )
        if not duty_is_published:
            unknown_lines.append(f"import duty for {category} into {destination}")

        vat_rate = _VAT_RATE.get(destination)
        vat_is_published = vat_rate is not None
        if vat_rate is None:
            vat_rate = _DEFAULT_VAT_RATE
            unknown_lines.append(f"import VAT rate for {destination}")
        vat_amount = ((taxable_base + shipping_amount + duty_amount) * vat_rate).quantize(CENTS)
        taxes = CostLine(
            amount=Money(amount=vat_amount, currency=currency),
            origin=CostOrigin.ESTIMATED if vat_is_published else CostOrigin.UNAVAILABLE,
            label=f"Estimated {destination} import VAT ({vat_rate:.0%})",
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
            list_ref.amount
            + sum((line.amount.amount for line in other_fees), Decimal("0"))
            + shipping_amount
            + duty_amount
            + vat_amount
            + registration_amount
        )
        if unknown_lines:
            completeness = LandedCostCompleteness.UNKNOWN
            logger.info(
                "Landed cost %s→%s (%s) marked unknown: %s",
                origin,
                destination,
                category,
                ", ".join(unknown_lines),
            )
        else:
            completeness = LandedCostCompleteness.PARTIAL
            if guessed_lines:
                logger.debug("Landed cost used fallback figures for %s", ", ".join(guessed_lines))

        return LandedCost(
            list_in_reference=list_ref,
            shipping=shipping,
            taxes=taxes,
            import_duties=import_duties,
            registration_fees=registration,
            other_fees=other_fees,
            total=Money(amount=total_amount, currency=currency),
            completeness=completeness,
            destination_country=destination,
        )

    @staticmethod
    def _duty_rate(destination: str, category: str) -> tuple[Decimal, bool]:
        """
        Duty rate plus whether it was actually published for this pair.

        A rate we made up is not the same as a rate we looked up, and the difference
        has to reach `completeness` instead of disappearing into the total.
        """
        override = _DUTY_RATE_BY_CATEGORY.get((destination, category))
        if override is not None:
            return override, True
        published = _DUTY_RATE.get(destination)
        if published is not None:
            return published, True
        return _DEFAULT_DUTY_RATE, False

    async def _flat_fee(self, amount: Decimal, currency: str) -> Decimal:
        """Restate a fee authored in ``FEE_CURRENCY`` into the reference currency."""
        if currency.upper() == FEE_CURRENCY:
            return amount.quantize(CENTS)
        converted = await self.fx.convert(
            Money(amount=amount, currency=FEE_CURRENCY), currency
        )
        return converted.reference.amount
