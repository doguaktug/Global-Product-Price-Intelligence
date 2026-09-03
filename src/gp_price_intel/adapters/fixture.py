"""Fixture adapter — pinned multi-country offers for demo markets."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

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
from gp_price_intel.ranking.confidence import compute_data_confidence_from

logger = logging.getLogger(__name__)


class FixtureAdapter(SourceAdapter):
    """Return curated offers from data/fixtures/offers.json."""

    def __init__(
        self,
        catalog: CatalogRepository | None = None,
        settings: Settings | None = None,
        sources: list[Source] | None = None,
        *,
        fixture_path: Path | None = None,
    ) -> None:
        self.catalog = catalog or CatalogRepository()
        self.settings = settings or get_settings()
        self.sources = sources or [
            Source(
                id="fixture-generic",
                display_name="Fixture",
                country="TR",
                kind=SourceKind.OTHER,
                reliability=0.5,
                acquisition_method=AcquisitionMethod.FIXTURE,
            )
        ]
        self.source_by_id = {source.id: source for source in self.sources}
        self.source = self.sources[0]
        self._fixture_path = fixture_path

    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        rows = self._load_rows()
        offers: list[Offer] = []
        allowed_variants = set(scope.variant_ids) if scope.variant_ids else None

        for row in rows:
            if row.get("family_id") != scope.family_id:
                continue
            variant_id = row.get("variant_id")
            if allowed_variants and variant_id not in allowed_variants:
                continue

            source_id = str(row.get("source_id", "fixture-generic"))
            source = self.source_by_id.get(source_id)
            if source is None:
                source = Source(
                    id=source_id,
                    display_name=source_id,
                    country=str(row.get("country", "TR")),
                    kind=SourceKind.OTHER,
                    reliability=float(row.get("seller_reliability", 0.7)),
                    acquisition_method=AcquisitionMethod.FIXTURE,
                )

            offer = self._row_to_offer(row, source)
            if offer.stock_status == StockStatus.OUT_OF_STOCK:
                continue
            offers.append(offer)
        return offers

    def _fixture_file(self) -> Path:
        if self._fixture_path is not None:
            return self._fixture_path
        return self.settings.data_dir / "fixtures" / "offers.json"

    def _load_rows(self) -> list[dict[str, Any]]:
        path = self._fixture_file()
        if not path.exists():
            logger.warning("Fixture file missing at %s", path)
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("offers", []))

    def _row_to_offer(self, row: dict[str, Any], source: Source) -> Offer:
        offer_id = str(row.get("id") or f"fixture-{uuid4()}")
        price = row["price"]
        currency = str(row.get("currency") or "EUR")
        stock = row.get("stock_status", "in_stock")
        stock_status = StockStatus(stock) if stock in StockStatus._value2member_map_ else StockStatus.UNKNOWN

        seller_name = str(row.get("seller_name") or source.display_name)
        review_count = row.get("review_count")
        seller = Seller(
            name=seller_name,
            reliability=row.get("seller_reliability", source.reliability),
            review_count=int(review_count) if review_count is not None else None,
            is_official=row.get("seller_is_official"),
        )
        if "data_confidence" in row:
            confidence = float(row["data_confidence"])
        else:
            confidence = compute_data_confidence_from(source, seller)

        return Offer(
            id=offer_id,
            source_id=source.id,
            seller=seller,
            country=str(row.get("country") or source.country),
            listing_title=str(row["listing_title"]),
            listing_url=str(row["listing_url"]),
            image_url=row.get("image_url"),
            list_price=Money(amount=Decimal(str(price)), currency=currency),
            retailer_sku=row.get("retailer_sku"),
            gtin=row.get("gtin"),
            model_number=row.get("model_number"),
            stock_status=stock_status,
            delivery_time=row.get("delivery_time"),
            warranty=row.get("warranty"),
            return_policy=row.get("return_policy"),
            raw_specs=[
                NormalizedSpec(key="title", value=row["listing_title"], raw_text=row["listing_title"]),
                *[
                    NormalizedSpec(key=key, value=row[key])
                    for key in ("storage_gb", "memory_gb", "region_version", "colour")
                    if key in row
                ],
            ],
            collected_at=datetime.now(timezone.utc),
            data_confidence=confidence,
        )
