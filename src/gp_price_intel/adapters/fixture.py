"""Fixture adapter — pinned multi-country offers for demo markets."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings, get_settings
from gp_price_intel.domain.models import (
    Money,
    NormalizedSpec,
    Offer,
    SearchScope,
    Seller,
    Source,
    StockStatus,
)
from gp_price_intel.normalize.spec_parser import parse_source_specs
from gp_price_intel.ranking.confidence import compute_data_confidence_from

logger = logging.getLogger(__name__)


class FixtureDataError(ValueError):
    """
    A fixture row is missing something no default can honestly supply.

    Fixture rows are authored, so an absent field is a mistake in the data rather
    than a condition to degrade around. Guessing one is worse than failing: a made
    up currency misreads the price, a made up source invents a seller's
    reliability, and both reach the user as a figure rather than a caveat. The
    orchestrator collects adapter failures per source, so a bad row is reported
    against its own file instead of taking the search down.
    """


def _required(row: dict[str, Any], key: str, offer_id: str) -> Any:
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise FixtureDataError(f"Fixture offer {offer_id!r} has no {key!r}.")
    return value


# Fixture columns carried into raw_specs so the matcher can compare them against
# catalog variants — including the category-specific keys.
_SPEC_ROW_KEYS = (
    "storage_gb",
    "memory_gb",
    "region_version",
    "colour",
    "processor",
    "connectivity",
    "display_inch",
    "battery_mah",
)


class FixtureAdapter(SourceAdapter):
    """Return curated offers from data/fixtures/offers.json."""

    def __init__(
        self,
        catalog: CatalogRepository | None = None,
        settings: Settings | None = None,
        *,
        sources: list[Source],
        fixture_path: Path | None = None,
    ) -> None:
        if not sources:
            raise FixtureDataError(
                "FixtureAdapter needs the sources its rows cite. With none, every "
                "offer's country and reliability would have to be invented, and "
                "reliability is a ranking input."
            )
        self.catalog = catalog or CatalogRepository()
        self.settings = settings or get_settings()
        self.sources = list(sources)
        self.source_by_id = {source.id: source for source in self.sources}
        self.source = self.sources[0]
        self._fixture_path = fixture_path

    def known_sources(self) -> list[Source]:
        return list(self.source_by_id.values())

    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        rows = self._load_rows()
        offers: list[Offer] = []

        for row in rows:
            offer_id = str(row.get("id") or "<unidentified row>")
            # A row with no family can never match any scope, so it would drop out of
            # every search silently rather than being reported once.
            family_id = str(_required(row, "family_id", offer_id))
            # Family is a hard boundary; other builds of the same family come back so
            # the matcher can offer them as close alternatives.
            if family_id != scope.family_id:
                continue

            source_id = str(_required(row, "source_id", offer_id))
            source = self.source_by_id.get(source_id)
            if source is None:
                raise FixtureDataError(
                    f"Fixture offer {offer_id!r} cites source {source_id!r}, which is "
                    "not in the source registry. Add it to data/sources/sources.json: "
                    "its reliability and country are ranking and landed-cost inputs, "
                    "so they cannot be stood in for."
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

    @staticmethod
    def _specs_from_row(row: dict[str, Any]) -> list[NormalizedSpec]:
        """
        Build this listing's specs, preferring what the "source" actually published.

        A fixture row can carry specs two ways. `source_specs` holds strings written
        the way a real retailer writes them — "5.000 mAh", "6,9 inç", "17,5 cm" — and
        goes through the unit parser, which is the path a live adapter takes. The
        typed columns (`storage_gb: 512`) are a curated shortcut for rows that do not
        need to exercise parsing.

        `source_specs` wins where both exist, because on a real source the published
        text *is* the data. Keeping the original string in `raw_text` lets the
        Decision Page show what the retailer said next to the normalized value.
        """
        published = parse_source_specs(row.get("source_specs"))
        raw_text = {
            key: str(value)
            for key, value in (row.get("source_specs") or {}).items()
            if isinstance(value, str)
        }

        specs: list[NormalizedSpec] = []
        for key in _SPEC_ROW_KEYS:
            if key in published:
                specs.append(
                    NormalizedSpec(key=key, value=published[key], raw_text=raw_text.get(key))
                )
            elif key in row:
                specs.append(NormalizedSpec(key=key, value=row[key]))
        return specs

    def _row_to_offer(self, row: dict[str, Any], source: Source) -> Offer:
        offer_id = str(_required(row, "id", "<unidentified row>"))
        price = _required(row, "price", offer_id)
        # A price without its currency is not a price. Reading one as EUR would
        # convert it at the wrong rate and rank it against correctly priced offers.
        currency = str(_required(row, "currency", offer_id))
        listing_title = str(_required(row, "listing_title", offer_id))
        listing_url = str(_required(row, "listing_url", offer_id))

        stock = row.get("stock_status")
        if stock is None:
            # Absent is "we do not know", which the confidence score already
            # discounts. Reading it as in-stock would be a guess in the seller's
            # favour on the one attribute the buyer cannot check for themselves.
            stock_status = StockStatus.UNKNOWN
        else:
            try:
                stock_status = StockStatus(stock)
            except ValueError as exc:
                raise FixtureDataError(
                    f"Fixture offer {offer_id!r} has stock_status {stock!r}. "
                    f"Expected one of {[s.value for s in StockStatus]}."
                ) from exc

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
            confidence = compute_data_confidence_from(source, seller, stock_status)

        return Offer(
            id=offer_id,
            source_id=source.id,
            seller=seller,
            country=str(row.get("country") or source.country),
            listing_title=listing_title,
            listing_url=listing_url,
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
                NormalizedSpec(key="title", value=listing_title, raw_text=listing_title),
                *self._specs_from_row(row),
            ],
            collected_at=datetime.now(timezone.utc),
            data_confidence=confidence,
        )
