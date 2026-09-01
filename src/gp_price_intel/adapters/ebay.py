"""eBay Browse API adapter."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings, get_settings
from gp_price_intel.domain.models import (
    AcquisitionMethod,
    Money,
    NormalizedSpec,
    Offer,
    SearchScope,
    Seller,
    Source,
    SourceKind,
    StockStatus,
)

logger = logging.getLogger(__name__)

EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE_COUNTRY = {
    "EBAY_US": "US",
    "EBAY_GB": "GB",
    "EBAY_DE": "DE",
    "EBAY_AU": "AU",
}


def default_ebay_source() -> Source:
    return Source(
        id="ebay",
        display_name="eBay",
        country="US",
        kind=SourceKind.MARKETPLACE,
        reliability=0.72,
        acquisition_method=AcquisitionMethod.API,
        base_url="https://www.ebay.com",
        notes="eBay Browse API (OAuth client credentials).",
    )


class EbayAdapter(SourceAdapter):
    """Search eBay listings via the official Browse API."""

    def __init__(
        self,
        source: Source | None = None,
        catalog: CatalogRepository | None = None,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        marketplace_id: str = "EBAY_US",
    ) -> None:
        self.source = source or default_ebay_source()
        self.catalog = catalog or CatalogRepository()
        self.settings = settings or get_settings()
        self.marketplace_id = marketplace_id
        self._client = client
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.settings.ebay_app_id and self.settings.ebay_cert_id)

    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        if not self.is_configured():
            logger.info("eBay adapter skipped — EBAY_APP_ID or EBAY_CERT_ID not set.")
            return []

        query = self._build_search_query(scope)
        if not query:
            return []

        try:
            token = await self._access_token()
            summaries = await self._search_items(token, query)
        except Exception:
            logger.exception("eBay search failed for query=%r", query)
            return []

        offers: list[Offer] = []
        for item in summaries:
            offer = self._parse_item(item)
            if offer is None or offer.stock_status == StockStatus.OUT_OF_STOCK:
                continue
            offers.append(offer)
        return offers

    async def check_availability(self, offer: Offer) -> dict[str, Any]:
        if not self.is_configured():
            return {"available": True, "verified": False, "message": "eBay credentials not configured."}

        item_id = offer.retailer_sku or offer.id.removeprefix("ebay-")
        try:
            token = await self._access_token()
            item = await self._get_item(token, item_id)
        except Exception:
            logger.exception("eBay availability check failed for %s", item_id)
            return {
                "available": True,
                "verified": False,
                "message": "Could not verify — confirm on eBay.",
            }

        if not item:
            return {
                "available": False,
                "verified": True,
                "message": "This listing no longer appears on eBay.",
            }

        parsed = self._parse_item(item)
        if parsed is None:
            return {"available": True, "verified": False, "message": "Could not parse live item."}

        return {
            "available": parsed.stock_status != StockStatus.OUT_OF_STOCK,
            "verified": True,
            "price": str(parsed.list_price.amount),
            "currency": parsed.list_price.currency,
            "message": "Availability re-checked on eBay.",
        }

    def _build_search_query(self, scope: SearchScope) -> str:
        family = self.catalog.get_family(scope.family_id)
        if family is None:
            return ""

        parts = [family.brand, family.family_name]
        storage = scope.constraints.get("storage_gb")
        if storage is not None:
            parts.append(f"{storage}GB")
        return " ".join(str(part) for part in parts)

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        host = self._api_host()
        credentials = f"{self.settings.ebay_app_id}:{self.settings.ebay_cert_id}"
        encoded = base64.b64encode(credentials.encode()).decode()
        client = await self._get_client()
        response = await client.post(
            f"https://{host}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 7200))
        return self._token

    async def _search_items(self, token: str, query: str) -> list[dict[str, Any]]:
        host = self._api_host()
        client = await self._get_client()
        response = await client.get(
            f"https://{host}/buy/browse/v1/item_summary/search",
            params={"q": query, "limit": "20"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        return list(response.json().get("itemSummaries", []))

    async def _get_item(self, token: str, item_id: str) -> dict[str, Any] | None:
        host = self._api_host()
        client = await self._get_client()
        response = await client.get(
            f"https://{host}/buy/browse/v1/item/{item_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            },
            timeout=10.0,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _api_host(self) -> str:
        return "api.sandbox.ebay.com" if self.settings.ebay_sandbox else "api.ebay.com"

    def _parse_item(self, item: dict[str, Any]) -> Offer | None:
        title = item.get("title")
        price_block = item.get("price") or {}
        amount = price_block.get("value")
        currency = price_block.get("currency")
        url = item.get("itemWebUrl")
        item_id = item.get("itemId")
        if not title or amount is None or not currency or not url or not item_id:
            return None

        seller_block = item.get("seller") or {}
        feedback = seller_block.get("feedbackPercentage")
        seller_score = float(feedback) / 100 if feedback is not None else None
        seller = Seller(
            name=str(seller_block.get("username") or "eBay seller"),
            reliability=seller_score,
            is_official=False,
        )

        image = (item.get("image") or {}).get("imageUrl")
        condition = item.get("condition")
        gtin = None
        for aspect in item.get("localizedAspects") or []:
            name = str(aspect.get("name", "")).casefold()
            if name in {"gtin", "ean", "upc"}:
                values = aspect.get("value") or []
                if values:
                    gtin = str(values[0])
                    break

        confidence = 0.75
        if seller_score is not None:
            confidence = min(1.0, 0.55 + seller_score * 0.45)

        return Offer(
            id=f"ebay-{item_id}",
            source_id=self.source.id,
            seller=seller,
            country=self._item_country(item),
            listing_title=str(title),
            listing_url=str(url),
            image_url=image,
            list_price=Money(amount=Decimal(str(amount)), currency=str(currency)),
            retailer_sku=str(item_id),
            gtin=gtin,
            stock_status=self._stock_status(item),
            warranty=None,
            return_policy=None,
            raw_specs=[
                NormalizedSpec(key="title", value=title, raw_text=str(title)),
                *(
                    [NormalizedSpec(key="condition", value=condition, raw_text=str(condition))]
                    if condition
                    else []
                ),
            ],
            collected_at=datetime.now(timezone.utc),
            data_confidence=confidence,
        )

    def _item_country(self, item: dict[str, Any]) -> str:
        location = item.get("itemLocation") or {}
        if location.get("country"):
            return str(location["country"])
        return MARKETPLACE_COUNTRY.get(self.marketplace_id, self.source.country)

    def _stock_status(self, item: dict[str, Any]) -> StockStatus:
        for availability in item.get("estimatedAvailabilities") or []:
            status = str(availability.get("estimatedAvailabilityStatus", "")).casefold()
            if status in {"in_stock", "available"}:
                return StockStatus.IN_STOCK
            if status in {"limited", "low_stock"}:
                return StockStatus.LIMITED
            if status in {"out_of_stock", "sold_out", "unavailable"}:
                return StockStatus.OUT_OF_STOCK
        return StockStatus.UNKNOWN

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
