"""FX service stub — Frankfurter / ECB rates in Week 2."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.domain.models import ConvertedMoney, FxQuote, Money


class FxService:
    """Convert Money without overwriting the original amount/currency."""

    async def convert(self, money: Money, reference_currency: str) -> ConvertedMoney:
        if money.currency.upper() == reference_currency.upper():
            quote = FxQuote(
                base_currency=money.currency,
                quote_currency=reference_currency,
                rate=Decimal("1"),
                as_of=datetime.now(timezone.utc),
                provider="identity",
            )
            return ConvertedMoney(original=money, reference=money, fx=quote)

        # Week 2: call Frankfurter (or configured provider).
        raise NotImplementedError(
            f"Live FX conversion {money.currency}→{reference_currency} not wired yet"
        )
