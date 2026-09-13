"""FX conversion must preserve the original and be honest about the rate's date."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from gp_price_intel.domain.models import Money
from gp_price_intel.fx.service import FxService


def _frankfurter(rate: str, date: str = "2026-09-11") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        return httpx.Response(
            200,
            json={
                "base": params["from"],
                "date": date,
                "rates": {params["to"]: float(rate)},
            },
        )

    return httpx.MockTransport(handler)


def _service(transport: httpx.MockTransport) -> FxService:
    return FxService(client=httpx.AsyncClient(transport=transport))


@pytest.mark.asyncio
async def test_same_currency_is_not_converted() -> None:
    """
    TRY→TRY must not invent a conversion.

    The rate is 1 and no provider was called, so there is no published rate date to
    show. Stamping "now" would tell the user we looked up a rate we never looked up.
    """
    service = _service(_frankfurter("999"))  # would be used if we wrongly converted
    converted = await service.convert(Money(amount=Decimal("52999"), currency="TRY"), "TRY")

    assert converted.reference.amount == Decimal("52999")
    assert converted.reference.currency == "TRY"
    assert converted.fx.rate == Decimal("1")
    assert converted.fx.is_identity
    assert converted.fx.as_of is None
    assert converted.fx.provider == "identity"


@pytest.mark.asyncio
async def test_conversion_reports_the_providers_publication_date() -> None:
    """`asOf` is when the ECB published the rate, not when we fetched it."""
    service = _service(_frankfurter("35.0", date="2026-09-11"))
    converted = await service.convert(Money(amount=Decimal("1349"), currency="EUR"), "TRY")

    assert converted.fx.as_of is not None
    assert converted.fx.as_of.date().isoformat() == "2026-09-11"
    assert converted.fx.provider == "frankfurter"
    assert not converted.fx.is_identity


@pytest.mark.asyncio
async def test_the_original_price_is_never_overwritten() -> None:
    """The assignment's core currency requirement: original stays visible."""
    original = Money(amount=Decimal("1349"), currency="EUR")
    converted = await _service(_frankfurter("35.0")).convert(original, "TRY")

    assert converted.original == original
    assert converted.reference.amount == Decimal("47215.00")
    assert converted.reference.currency == "TRY"
