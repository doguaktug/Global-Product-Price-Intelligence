"""Landed cost must price a laptop differently from a handset."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from gp_price_intel.domain.models import (
    ConvertedMoney,
    FxQuote,
    LandedCostCompleteness,
    Money,
)
from gp_price_intel.landed_cost.service import LandedCostService

_RATES_TO_TRY = {"USD": Decimal("32"), "EUR": Decimal("35")}


class _FakeFx:
    """Converts flat fee currencies without touching the network."""

    async def convert(self, money: Money, reference_currency: str) -> ConvertedMoney:
        ref = reference_currency.upper()
        base = money.currency.upper()
        rate = Decimal("1") if base == ref else _RATES_TO_TRY[base]
        return ConvertedMoney(
            original=money,
            reference=Money(amount=(money.amount * rate).quantize(Decimal("0.01")), currency=ref),
            fx=FxQuote(
                base_currency=base,
                quote_currency=ref,
                rate=rate,
                as_of=datetime.now(timezone.utc),
                provider="test",
            ),
        )


def _converted(amount: str, currency: str = "TRY") -> ConvertedMoney:
    money = Money(amount=Decimal(amount), currency=currency)
    return ConvertedMoney(
        original=money,
        reference=money,
        fx=FxQuote(
            base_currency=currency,
            quote_currency=currency,
            rate=Decimal("1"),
            as_of=datetime.now(timezone.utc),
            provider="identity",
        ),
    )


@pytest.fixture
def service() -> LandedCostService:
    return LandedCostService(fx=_FakeFx())


@pytest.mark.asyncio
async def test_turkish_handset_import_carries_a_registration_fee(
    service: LandedCostService,
) -> None:
    landed = await service.estimate(_converted("40000"), "DE", "TR", "smartphone")

    assert landed.registration_fees is not None
    assert landed.registration_fees.amount.amount > 0
    assert landed.registration_fees.amount.currency == "TRY"
    assert "registration" in landed.registration_fees.label.casefold()


@pytest.mark.asyncio
async def test_laptop_and_tablet_imports_are_not_registered(
    service: LandedCostService,
) -> None:
    for category in ("laptop", "tablet"):
        landed = await service.estimate(_converted("40000"), "DE", "TR", category)
        assert landed.registration_fees is None, category


@pytest.mark.asyncio
async def test_duty_rate_depends_on_the_category(service: LandedCostService) -> None:
    phone = await service.estimate(_converted("40000"), "DE", "TR", "smartphone")
    tablet = await service.estimate(_converted("40000"), "DE", "TR", "tablet")
    laptop = await service.estimate(_converted("40000"), "DE", "TR", "laptop")

    assert phone.import_duties is not None
    assert tablet.import_duties is not None
    assert laptop.import_duties is not None
    assert phone.import_duties.amount.amount > tablet.import_duties.amount.amount
    assert laptop.import_duties.amount.amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_shipping_scales_with_parcel_size(service: LandedCostService) -> None:
    phone = await service.estimate(_converted("40000"), "DE", "TR", "smartphone")
    tablet = await service.estimate(_converted("40000"), "DE", "TR", "tablet")
    laptop = await service.estimate(_converted("40000"), "DE", "TR", "laptop")

    assert phone.shipping is not None
    assert tablet.shipping is not None
    assert laptop.shipping is not None
    ship = [c.shipping.amount.amount for c in (phone, tablet, laptop)]  # type: ignore[union-attr]
    assert ship == sorted(ship)
    assert ship[0] < ship[2]


@pytest.mark.asyncio
async def test_flat_fees_are_restated_in_the_reference_currency(
    service: LandedCostService,
) -> None:
    in_try = await service.estimate(_converted("40000", "TRY"), "DE", "TR", "smartphone")
    in_usd = await service.estimate(_converted("1250", "USD"), "DE", "TR", "smartphone")

    assert in_try.registration_fees is not None
    assert in_usd.registration_fees is not None
    assert in_try.registration_fees.amount.currency == "TRY"
    assert in_usd.registration_fees.amount.currency == "USD"
    # Same fee, two currencies: the TRY figure must be ~32x the USD one, not equal.
    assert in_try.registration_fees.amount.amount == (
        in_usd.registration_fees.amount.amount * _RATES_TO_TRY["USD"]
    )


@pytest.mark.asyncio
async def test_domestic_purchase_adds_no_import_costs(service: LandedCostService) -> None:
    landed = await service.estimate(_converted("52999"), "TR", "TR", "smartphone")

    assert landed.completeness == LandedCostCompleteness.COMPLETE
    assert landed.total.amount == Decimal("52999")
    assert landed.registration_fees is None
    assert landed.import_duties is None


@pytest.mark.asyncio
async def test_total_is_the_sum_of_every_line(service: LandedCostService) -> None:
    landed = await service.estimate(_converted("40000"), "DE", "TR", "smartphone")

    lines = [landed.shipping, landed.taxes, landed.import_duties, landed.registration_fees]
    expected = landed.list_in_reference.amount + sum(
        line.amount.amount for line in lines if line is not None
    )
    assert landed.total.amount == expected
    assert landed.completeness == LandedCostCompleteness.PARTIAL
