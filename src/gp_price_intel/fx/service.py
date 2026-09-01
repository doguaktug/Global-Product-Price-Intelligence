"""FX conversion via Frankfurter (ECB rates)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from gp_price_intel.config import Settings, get_settings
from gp_price_intel.domain.models import ConvertedMoney, FxQuote, Money

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.app/latest"


class FxService:
    """Convert Money without overwriting the original amount/currency."""

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        self._cache: dict[tuple[str, str], tuple[FxQuote, float]] = {}
        self._cache_ttl_seconds = 3600

    async def convert(self, money: Money, reference_currency: str) -> ConvertedMoney:
        ref = reference_currency.upper()
        base = money.currency.upper()
        if base == ref:
            quote = FxQuote(
                base_currency=base,
                quote_currency=ref,
                rate=Decimal("1"),
                as_of=datetime.now(timezone.utc),
                provider="identity",
            )
            return ConvertedMoney(original=money, reference=money, fx=quote)

        rate_quote = await self._get_rate(base, ref)
        converted_amount = (money.amount * rate_quote.rate).quantize(Decimal("0.01"))
        reference = Money(amount=converted_amount, currency=ref)
        return ConvertedMoney(original=money, reference=reference, fx=rate_quote)

    async def _get_rate(self, base: str, quote: str) -> FxQuote:
        cache_key = (base, quote)
        cached = self._cache.get(cache_key)
        if cached and time.time() < cached[1]:
            return cached[0]

        client = await self._get_client()
        try:
            response = await client.get(
                FRANKFURTER_URL,
                params={"from": base, "to": quote},
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
            rate = Decimal(str(payload["rates"][quote]))
            as_of = datetime.fromisoformat(str(payload["date"])).replace(tzinfo=timezone.utc)
            quote_obj = FxQuote(
                base_currency=base,
                quote_currency=quote,
                rate=rate,
                as_of=as_of,
                provider="frankfurter",
            )
        except Exception:
            logger.exception("Frankfurter FX failed for %s→%s", base, quote)
            raise

        self._cache[cache_key] = (quote_obj, time.time() + self._cache_ttl_seconds)
        return quote_obj

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
