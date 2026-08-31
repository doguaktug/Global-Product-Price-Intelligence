"""Fnac Spain (fnac.es) source adapter — reference implementation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

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

# Demo fixture used when FNAC_API_KEY is not set (Fnac has no public consumer API).
DEFAULT_FNAC_ES_FIXTURE_NAME = "fnac_es_search_sample.json"

# Map Fnac availability strings → our StockStatus.
_AVAILABILITY_MAP: dict[str, StockStatus] = {
    "in_stock": StockStatus.IN_STOCK,
    "instock": StockStatus.IN_STOCK,
    "available": StockStatus.IN_STOCK,
    "limited": StockStatus.LIMITED,
    "low_stock": StockStatus.LIMITED,
    "out_of_stock": StockStatus.OUT_OF_STOCK,
    "unavailable": StockStatus.OUT_OF_STOCK,
}


def default_fnac_es_source() -> Source:
    return Source(
        id="fnac-es",
        display_name="Fnac España",
        country="ES",
        kind=SourceKind.AUTHORIZED_RETAILER,
        reliability=0.88,
        acquisition_method=AcquisitionMethod.API,
        base_url="https://www.fnac.es",
        notes="Live when FNAC_API_KEY is set; otherwise demo fixture JSON.",
    )


class FnacEsAdapter(SourceAdapter):
    """
    Fetch offers from Fnac Spain.

    **Important:** Fnac does not offer a free public product-search API like Best Buy.
    Real integrations usually go through a partner/aggregator feed or marketplace seller API.

    This adapter shows the pattern:
    1. Build a search query from ``SearchScope`` + catalog family name
    2. ``_fetch_raw_results`` — HTTP when configured, else pinned fixture JSON
    3. ``_parse_listing`` — map each row to our ``Offer`` model
    4. Soft-fail — errors log and return ``[]``
    """

    def __init__(
        self,
        source: Source | None = None,
        catalog: CatalogRepository | None = None,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        fixture_path: Path | None = None,
    ) -> None:
        self.source = source or default_fnac_es_source()
        self.catalog = catalog or CatalogRepository()
        self.settings = settings or get_settings()
        self._client = client
        self._fixture_path = fixture_path
        self._owns_client = client is None

    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        query = self._build_search_query(scope)
        if not query:
            return []

        try:
            raw_results = await self._fetch_raw_results(query)
        except Exception:
            logger.exception("Fnac ES adapter failed for query=%r", query)
            return []

        offers: list[Offer] = []
        for raw in raw_results:
            offer = self._parse_listing(raw)
            if offer is None:
                continue
            if offer.stock_status == StockStatus.OUT_OF_STOCK:
                continue
            offers.append(offer)
        return offers

    async def check_availability(self, offer: Offer) -> dict[str, Any]:
        """Re-fetch a single product when the user clicks through (live mode only)."""
        if not self.settings.fnac_api_key:
            return {
                "available": offer.stock_status != StockStatus.OUT_OF_STOCK,
                "verified": False,
                "message": "Demo fixture mode — live re-check needs FNAC_API_KEY.",
            }

        product_id = offer.id.removeprefix("fnac-es-")
        try:
            raw = await self._fetch_product_by_id(product_id)
        except Exception:
            logger.exception("Fnac availability check failed for %s", product_id)
            return {
                "available": True,
                "verified": False,
                "message": "Could not verify — confirm on fnac.es.",
            }

        if raw is None:
            return {
                "available": False,
                "verified": True,
                "message": "This listing no longer appears on Fnac.",
            }

        parsed = self._parse_listing(raw)
        if parsed is None:
            return {
                "available": True,
                "verified": False,
                "message": "Could not parse live response.",
            }

        return {
            "available": parsed.stock_status != StockStatus.OUT_OF_STOCK,
            "verified": True,
            "price": str(parsed.list_price.amount),
            "currency": parsed.list_price.currency,
            "message": "Availability re-checked on Fnac.",
        }

    def _build_search_query(self, scope: SearchScope) -> str:
        family = self.catalog.get_family(scope.family_id)
        if family is None:
            return ""

        parts = [family.brand, family.family_name]
        storage = scope.constraints.get("storage_gb")
        if storage is not None:
            parts.append(f"{storage} GB")
        memory = scope.constraints.get("memory_gb")
        if memory is not None:
            parts.append(f"{memory} GB RAM")

        return " ".join(str(part) for part in parts)

    async def _fetch_raw_results(self, query: str) -> list[dict[str, Any]]:
        if self.settings.fnac_api_key:
            return await self._fetch_live_search(query)
        return self._load_fixture_results(query)

    async def _fetch_live_search(self, query: str) -> list[dict[str, Any]]:
        """
        Call your partner/aggregator Fnac search endpoint.

        Configure ``FNAC_API_BASE_URL`` to the base URL your provider documents
        (not fnac.es HTML — that is out of scope for this project).
        """
        base_url = (self.settings.fnac_api_base_url or "").rstrip("/")
        if not base_url:
            logger.warning("FNAC_API_KEY set but FNAC_API_BASE_URL missing — using fixture.")
            return self._load_fixture_results(query)

        client = await self._get_client()
        response = await client.get(
            f"{base_url}/search",
            params={"q": query, "country": "es"},
            headers={"Authorization": f"Bearer {self.settings.fnac_api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("results", payload.get("products", [])))

    async def _fetch_product_by_id(self, product_id: str) -> dict[str, Any] | None:
        base_url = (self.settings.fnac_api_base_url or "").rstrip("/")
        if not base_url:
            return None

        client = await self._get_client()
        response = await client.get(
            f"{base_url}/products/{product_id}",
            headers={"Authorization": f"Bearer {self.settings.fnac_api_key}"},
            timeout=5.0,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _fixture_file(self) -> Path:
        if self._fixture_path is not None:
            return self._fixture_path
        return self.settings.data_dir / "fixtures" / DEFAULT_FNAC_ES_FIXTURE_NAME

    def _load_fixture_results(self, query: str) -> list[dict[str, Any]]:
        fixture_path = self._fixture_file()
        if not fixture_path.exists():
            logger.warning("Fnac fixture missing at %s", fixture_path)
            return []

        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        results: list[dict[str, Any]] = list(payload.get("results", []))
        query_tokens = {token.casefold() for token in query.split() if len(token) > 2}

        if not query_tokens:
            return results

        def matches(row: dict[str, Any]) -> bool:
            title = str(row.get("title", "")).casefold()
            return any(token in title for token in query_tokens)

        filtered = [row for row in results if matches(row)]
        return filtered or results[:2]

    def _parse_listing(self, raw: dict[str, Any]) -> Offer | None:
        title = raw.get("title")
        url = raw.get("url")
        price = raw.get("price")
        if not title or not url or price is None:
            return None

        product_id = str(raw.get("product_id") or uuid4())
        currency = str(raw.get("currency") or "EUR")
        stock = _AVAILABILITY_MAP.get(
            str(raw.get("availability", "unknown")).casefold().replace(" ", "_"),
            StockStatus.UNKNOWN,
        )

        seller_name = str(raw.get("seller_name") or "Fnac")
        seller = Seller(
            name=seller_name,
            reliability=self.source.reliability,
            is_official=raw.get("seller_is_official", True),
        )

        rating = raw.get("rating")
        review_count = raw.get("review_count")
        confidence = 0.85
        if rating is not None and review_count:
            confidence = min(1.0, 0.7 + float(rating) / 20)

        return Offer(
            id=f"fnac-es-{product_id}",
            source_id=self.source.id,
            seller=seller,
            country=self.source.country,
            listing_title=str(title),
            listing_url=str(url),
            image_url=raw.get("image_url"),
            list_price=Money(amount=Decimal(str(price)), currency=currency),
            stock_status=stock,
            delivery_time=raw.get("delivery_hint"),
            raw_specs=[NormalizedSpec(key="title", value=title, raw_text=str(title))],
            collected_at=datetime.now(timezone.utc),
            data_confidence=confidence,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
