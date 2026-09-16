"""Source adapter contract and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod

from gp_price_intel.domain.models import Offer, SearchScope, Source


class SourceFetchError(RuntimeError):
    """
    A source could not be consulted, as opposed to having nothing to offer.

    The orchestrator keeps going on this — one dead source must not sink a search
    that other sources can still answer — but it records the reason. Returning an
    empty list instead would make "your credentials are rejected" indistinguishable
    from "this product is not listed here", which is the one distinction someone
    debugging a live adapter actually needs.
    """


class SourceAdapter(ABC):
    """
    One retailer/API/fixture per adapter.

    Return `[]` when the source answered and had nothing. Raise `SourceFetchError`
    when the source could not be reached or refused the request.
    """

    source: Source

    @abstractmethod
    async def search(
        self,
        scope: SearchScope,
        destination_country: str,
        *,
        include_used: bool = False,
    ) -> list[Offer]:
        """Fetch listings for the search scope.

        ``include_used`` is a user preference: when False, adapters may drop
        used / refurbished / open-box stock; when True they must keep them.
        """

    def known_sources(self) -> list[Source]:
        """
        Every source this adapter can stamp on an offer.

        Ranking needs the `Source` behind `Offer.sourceId` to weigh site reputation,
        and an adapter may speak for more than one site.
        """
        return [self.source]

    def unavailable_reason(self) -> str | None:
        """
        Why this adapter cannot search at all, or `None` when it can.

        A source that is switched off contributes nothing, which looks exactly like a
        source that had nothing — so an empty Decision Page can say which sources were
        never asked instead of implying the product is unlisted everywhere.
        """
        return None
