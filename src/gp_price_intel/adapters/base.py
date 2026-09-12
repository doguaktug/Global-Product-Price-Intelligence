"""Source adapter contract and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from gp_price_intel.domain.models import Offer, SearchScope, Source


class SourceAdapter(ABC):
    """One retailer/API/fixture per adapter. Soft-fail: return [] on error."""

    source: Source

    @abstractmethod
    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        """Fetch listings for the search scope."""

    def known_sources(self) -> list[Source]:
        """
        Every source this adapter can stamp on an offer.

        Ranking needs the `Source` behind `Offer.sourceId` to weigh site reputation,
        and an adapter may speak for more than one site.
        """
        return [self.source]

    async def check_availability(self, offer: Offer) -> dict[str, Any]:
        """Lightweight on-click re-check. Override in live adapters."""
        return {
            "available": offer.stock_status.value != "out_of_stock",
            "verified": False,
            "message": "Re-check not implemented for this source.",
        }
